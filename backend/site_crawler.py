"""Finds report PDFs by reading a company's own website.

Search engines often return nothing to requests from cloud servers, so this goes
to the source instead:

  1. robots.txt -> sitemaps -> URLs that look like reports or ESG/investor pages
  2. the home page and a few common paths (/sustainability, /investors, ...)
  3. a small breadth-first crawl of ESG/investor pages, best-looking links first

HTML pages are fetched only on the company's official domains and robots.txt is
respected. PDF links found on those pages are collected (not downloaded), even when
the PDF itself sits on a CDN, because the company's own page links to it.
The crawl is bounded by page count and a time budget.
"""
from __future__ import annotations

import gzip
import re
import time
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}
MAX_BYTES = 3_000_000

# Path words that mark a page worth crawling, with a priority.
PAGE_TERMS = {
    "brsr": 9, "business-responsibility": 9, "esg": 8, "sustainab": 8, "responsib": 6, "impact": 5,
    "climate": 5, "annual-report": 7, "integrated-report": 7, "reports": 5, "report": 4,
    "investor": 4, "csr": 4, "environment": 3, "downloads": 3, "disclosures": 3, "governance": 2,
    "about": 1,
}
# Words that mark a PDF as an ESG-relevant report.
PDF_TERMS = ("brsr", "business responsibility", "business-responsibility", "sustainab", "esg", "integrated",
             "annual report", "annual-report", "annualreport", "climate", "impact report", "impact-report",
             "tcfd", "cdp", "responsib", "environment", "csr", "non-financial", "people report")
SKIP_PATH = re.compile(r"/(careers?|jobs|news|press|media|blogs?|events?|login|signin|search|cart|shop|"
                       r"products?|privacy|cookies?|terms|legal)(/|$)|\.(jpg|jpeg|png|gif|svg|webp|mp4|zip|css|js)$",
                       re.I)
GENERIC_ANCHORS = {"", "download", "pdf", "read more", "view", "click here", "here", "know more", "learn more",
                   "view report", "download pdf", "download report", "read", "open"}
COMMON_PATHS = ("/sustainability", "/esg", "/investors", "/investor-relations", "/sustainability/reports")


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self._href, self._text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            a = dict(attrs)
            if a.get("href"):
                self._href, self._text = a["href"], [a.get("title") or a.get("aria-label") or ""]

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(" ".join(self._text).split())))
            self._href = None


_EMBEDDED_PDF = re.compile(r"""["'(]((?:https?:)?//[^"'\s<>()]+?\.pdf|/[^"'\s<>()]+?\.pdf)(?:[?#][^"'\s<>()]*)?["')]""", re.I)


def extract_links(html: str, base: str) -> list:
    """(absolute_url, anchor_text) for <a> tags plus PDF paths embedded in scripts or JSON."""
    p = _Links()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML still yields the links parsed so far
        pass
    out = [(href, text) for href, text in p.links]
    out += [(m.group(1).replace("\\/", "/"), "") for m in _EMBEDDED_PDF.finditer(html.replace("\\/", "/"))]
    links, seen = [], set()
    for href, text in out:
        href = unescape(href.strip())
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urldefrag(urljoin(base, href))[0]
        if url.startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            links.append((url, text))
    return links


def is_pdf(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(".pdf") or ".pdf/" in path


def host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def on_domains(url: str, domains) -> bool:
    h = host(url)
    return any(h == d or h.endswith("." + d) for d in domains)


def page_priority(url: str, text: str = "") -> int:
    s = f"{urlparse(url).path} {text}".lower().replace(" ", "-")
    return sum(w for t, w in PAGE_TERMS.items() if t in s)


NOT_REPORT = re.compile(r"polic|charter|code-of-conduct|code of conduct|notice|form-?\d|application|brochure|"
                        r"factsheet|presentation|transcript|tender|certificate", re.I)


def pdf_is_report(url: str, text: str) -> bool:
    """ESG-related report, not a policy, notice or brochure (unless it says it is a report)."""
    s = f"{urlparse(url).path} {text}".lower()
    if not any(t in s for t in PDF_TERMS):
        return False
    return "report" in s or "brsr" in s or not NOT_REPORT.search(s)


def title_for(url: str, anchor: str) -> str:
    a = " ".join(anchor.split())
    if a.lower() not in GENERIC_ANCHORS and 4 <= len(a) <= 160 and not a.lower().startswith("http"):
        return a
    name = urlparse(url).path.rstrip("/").split("/")[-1]
    name = re.sub(r"\.pdf$", "", name, flags=re.I)
    name = re.sub(r"(?:%20|[-_+])+", " ", name).strip()
    return name[:1].upper() + name[1:] if name else "Report"


def http_fetch(url: str, timeout: float = 8.0):
    """Returns (final_url, content_type, text) or None. Reads at most MAX_BYTES."""
    import requests

    try:
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=(5, timeout), stream=True, allow_redirects=True)
        if r.status_code >= 400:
            return None
        body = b""
        for chunk in r.iter_content(1 << 16):
            body += chunk
            if len(body) > MAX_BYTES:
                break
        ctype = r.headers.get("content-type", "").lower()
        if url.endswith(".gz") or body[:2] == b"\x1f\x8b":
            try:
                body = gzip.decompress(body)
            except OSError:
                return None
        return r.url, ctype, body.decode(r.encoding or "utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - an unreachable page is skipped, not fatal
        return None


def _robots(base: str, fetch):
    rp = RobotFileParser()
    got = fetch(base + "/robots.txt")
    text = got[2] if got and "<html" not in got[2][:500].lower() else ""
    rp.parse(text.splitlines())
    sitemaps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", text)
    return rp, sitemaps


def _sitemap_urls(sitemaps: list, base: str, fetch, deadline: float, limit_files: int = 6) -> list:
    """URLs from sitemaps; sitemap indexes are followed, ESG-looking children first."""
    queue = list(dict.fromkeys(sitemaps or [base + "/sitemap.xml"]))
    urls, done = [], 0
    while queue and done < limit_files and time.time() < deadline:
        got = fetch(queue.pop(0))
        done += 1
        if not got:
            continue
        locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", got[2], re.S | re.I)
        if "<sitemapindex" in got[2][:2000].lower():
            children = sorted((unescape(l) for l in locs), key=lambda u: -page_priority(u))
            queue = children[:limit_files] + queue
        else:
            urls += [unescape(l) for l in locs]
    return urls


def crawl(company: dict, fetch=http_fetch, max_pages: int = 24, budget_s: float = 28.0, workers: int = 6) -> dict:
    """Returns {"pdfs": [{url, title, found_on}], "pages": [{url, title}], "notes": [...]}."""
    deadline = time.time() + budget_s
    domains = [d for d in company["domains"] if d != "gstatic.com"]
    base = f"https://{domains[0]}"
    notes, pdfs, pages = [], {}, {}

    rp, sitemaps = _robots(base, fetch)
    allowed = lambda u: rp.can_fetch(BROWSER_HEADERS["User-Agent"], u) if rp.default_entry or rp.entries else True

    def take_pdf(url, text, found_on):
        if pdf_is_report(url, text) and url not in pdfs and url.startswith("https://"):
            pdfs[url] = {"url": url, "title": title_for(url, text), "found_on": found_on}

    # 1. sitemap
    queue = {}
    for u in _sitemap_urls(sitemaps, base, fetch, deadline):
        if not on_domains(u, domains) and not is_pdf(u):
            continue
        if is_pdf(u):
            take_pdf(u, "", "sitemap")
        elif not SKIP_PATH.search(urlparse(u).path) and page_priority(u) >= 4:
            queue[u] = page_priority(u)
    notes.append(f"sitemap: {len(pdfs)} report PDFs, {len(queue)} ESG/investor pages")

    # 2. home page and common paths
    for d in domains[:2]:
        root = f"https://{d}"
        queue.setdefault(root + "/", 100)
        for p in COMMON_PATHS:
            queue.setdefault(root + p, 50)

    # 3. bounded crawl, highest priority first
    seen, fetched = set(), 0
    while queue and fetched < max_pages and time.time() < deadline:
        batch = []
        for u, _ in sorted(queue.items(), key=lambda kv: -kv[1]):
            if u not in seen and allowed(u):
                batch.append(u)
            seen.add(u)
            if len(batch) >= min(workers, max_pages - fetched):
                break
        for u in batch + [u for u in list(queue) if u in seen]:
            queue.pop(u, None)
        if not batch:
            break
        with ThreadPoolExecutor(workers) as pool:
            results = list(pool.map(fetch, batch))
        fetched += len(batch)
        for asked, got in zip(batch, results):
            if not got:
                continue
            final, ctype, text = got
            if not on_domains(final, domains):  # redirected off the official site
                continue
            if "html" not in ctype and "<html" not in text[:3000].lower():
                continue
            title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
            title = " ".join(unescape(title.group(1)).split())[:160] if title else ""
            if page_priority(final, title) >= 8 and final not in pages and final.rstrip("/") != base:
                pages[final] = {"url": final, "title": title or title_for(final, "")}
            for url, anchor in extract_links(text, final):
                if is_pdf(url):
                    take_pdf(url, anchor, final)
                elif (on_domains(url, domains) and url not in seen
                      and not SKIP_PATH.search(urlparse(url).path)):
                    pr = page_priority(url, anchor)
                    if pr >= 4:
                        queue[url] = max(queue.get(url, 0), pr)
    notes.append(f"website: {fetched} pages read, {len(pdfs)} report PDFs found")
    return {"pdfs": list(pdfs.values()), "pages": list(pages.values()), "notes": notes}
