"""Finds ESG, sustainability, BRSR and annual reports for a company from a curated list.

Only companies in companies.json can be searched. Each has its official website
domains, and a result is kept only when it is hosted on one of them (or, for
Indian BRSR filings, on the stock exchange with the company named in the title).
This is what stops other companies' pages from showing up.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

CATALOG_PATH = Path(__file__).resolve().parent.parent / "companies.json"

# Exchange filing hosts serve every listed company, so a match also needs the company's name.
FILING_HOSTS = ("nseindia.com", "bseindia.com")

REPORT_TERMS = {
    "brsr": 10, "business responsibility": 10, "sustainability report": 10, "esg report": 10,
    "integrated report": 9, "climate report": 8, "impact report": 7, "environmental report": 8,
    "responsibility report": 8, "annual report": 6, "sustainability": 5, "esg": 4, "climate": 2,
}
BLOCK_TERMS = ("career", "/jobs", "job opening", "hiring", "/news/", "press-release", "/blog", "login",
               "privacy-policy", "cookie", "/products/", "/shop")


class UnknownCompany(ValueError):
    pass


@lru_cache(maxsize=1)
def load_catalog() -> list:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9&]+", " ", s.lower()).strip()


def resolve(query: str) -> dict:
    """Matches a name, alias or id from the catalog. Raises UnknownCompany otherwise."""
    q = _key(query)
    for c in load_catalog():
        if q in {_key(c["id"]), _key(c["name"]), *(_key(a) for a in c["aliases"])}:
            return c
    raise UnknownCompany(f'"{query}" is not in the supported company list. Pick a company from the list.')


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def on_domain(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def _names(c: dict) -> list:
    return [_key(n) for n in (c["name"], *c["aliases"]) if len(n) > 2]


def accept(c: dict, url: str, title: str, summary: str) -> bool:
    """True only for report-like documents that clearly belong to this company."""
    if not url.startswith("https://"):
        return False
    host, text = host_of(url), f"{title} {summary} {url}".lower()
    if any(b in text for b in BLOCK_TERMS):
        return False
    if not any(t in text for t in REPORT_TERMS):
        return False
    if on_domain(host, c["domains"]):
        return True
    if on_domain(host, FILING_HOSTS):
        return any(n in _key(title) for n in _names(c))
    return False


def score(url: str, title: str, summary: str) -> int:
    text = f"{title} {summary} {url}".lower()
    s = max((w for t, w in REPORT_TERMS.items() if t in text), default=0)
    s += 4 if url.lower().split("?")[0].endswith(".pdf") else 0
    return s


def report_year(text: str) -> str:
    """Latest year in a title or URL, e.g. 'FY2024-25' -> '2024-25'."""
    years = re.findall(r"(20[12]\d)(?:\s*[-–/]\s*(\d{2,4}))?", text)
    if not years:
        return ""
    y, tail = max(years, key=lambda m: m[0])
    return f"{y}-{tail[-2:]}" if tail else y


def report_type(text: str) -> str:
    t = text.lower()
    for key, label in (("brsr", "BRSR"), ("business responsibility", "BRSR"), ("integrated", "Integrated Report"),
                       ("climate", "Climate Report"), ("impact report", "Impact Report"),
                       ("sustainability", "Sustainability Report"), ("esg", "ESG Report"),
                       ("annual report", "Annual Report")):
        if key in t:
            return label
    return "Company Report"


def queries_for(c: dict) -> list:
    d = c["domains"][0]
    qs = [f'"{c["name"]}" sustainability report pdf', f"site:{d} sustainability report",
          f'"{c["name"]}" ESG report', f"site:{d} annual report pdf"]
    if c["region"] == "India":
        qs.insert(1, f'"{c["name"]}" BRSR business responsibility and sustainability report')
    return qs


def _ddgs_search(query: str, region: str) -> list:
    from ddgs import DDGS
    with DDGS(timeout=8) as search:
        return list(search.text(query, region=region, safesearch="moderate", max_results=10) or [])


def _engine(c: dict, search, deadline: float) -> tuple[list, str]:
    """Results from the web search engine, kept only if they pass `accept`."""
    region = "in-en" if c["region"] == "India" else "wt-wt"
    out, ok, failed = [], 0, 0
    for q in queries_for(c):
        if time.time() > deadline:
            break
        try:
            items = search(q, region)
            ok += 1
        except Exception:  # noqa: BLE001 - search engines often refuse cloud servers
            failed += 1
            continue
        for item in items or []:
            url, title = str(item.get("href", "")).strip(), str(item.get("title", "")).strip()
            summary = str(item.get("body", "")).strip()
            if title and accept(c, url, title, summary):
                out.append({"url": url, "title": title, "summary": summary, "via": "search"})
    return out, f"search engine: {ok} queries answered, {failed} failed, {len(out)} matching results"


def _site(c: dict, crawl_fn) -> tuple[list, str]:
    got = crawl_fn(c)
    out = [{"url": p["url"], "title": p["title"], "via": "website",
            "summary": f"Linked from {p['found_on']}" if p["found_on"].startswith("http") else "Listed in the site map"}
           for p in got["pdfs"]]
    out += [{"url": p["url"], "title": p["title"], "via": "website",
             "summary": "ESG / investor page on the official website"} for p in got["pages"]]
    return out, "; ".join(got["notes"])


CACHE_S = 6 * 3600
_CACHE: dict = {}


def search_company_reports(company: str, search=_ddgs_search, crawl_fn=None, use_cache: bool = True) -> dict:
    """Crawls the official website and queries a search engine in parallel, then merges."""
    from backend.site_crawler import crawl

    c = resolve(company)
    hit = _CACHE.get(c["id"])
    if use_cache and hit and time.time() - hit[0] < CACHE_S:
        return hit[1]

    deadline = time.time() + 30
    with ThreadPoolExecutor(2) as pool:
        site_job = pool.submit(_site, c, crawl_fn or crawl)
        engine_job = pool.submit(_engine, c, search, deadline)
        notes = []
        found = []
        for job in (site_job, engine_job):
            try:
                rows, note = job.result()
                found += rows
                notes.append(note)
            except Exception as e:  # noqa: BLE001 - one source failing must not hide the other
                notes.append(f"{'website' if job is site_job else 'search engine'}: failed ({type(e).__name__})")

    seen, results = set(), []
    for r in found:
        key = r["url"].split("#")[0].split("?")[0].rstrip("/").lower().replace("://www.", "://")
        if key in seen:
            continue
        seen.add(key)
        text = f"{r['title']} {r['url']}"
        pdf = r["url"].lower().split("?")[0].endswith(".pdf")
        results.append({
            "title": r["title"], "year": report_year(text) or "See document", "type": report_type(text),
            "url": r["url"], "source": host_of(r["url"]), "summary": r["summary"], "pdf": pdf, "via": r["via"],
            "_score": score(r["url"], r["title"], r["summary"]) + (3 if pdf else -4),
        })
    results.sort(key=lambda r: (r["pdf"], r["year"] if r["year"][:2] == "20" else "0", r["_score"]), reverse=True)
    for r in results:
        del r["_score"]
    out = {
        "company": c["name"], "company_id": c["id"], "official_site": f"https://{c['domains'][0]}",
        "reports": results[:15], "count": min(len(results), 15), "checked": notes,
    }
    if results:  # do not cache an empty answer: the next try may succeed
        _CACHE[c["id"]] = (time.time(), out)
    return out
