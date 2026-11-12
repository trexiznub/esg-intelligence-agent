"""Finds ESG, sustainability, BRSR and annual reports for a company from a curated list.

Only companies in companies.json can be searched. Each has its official website
domains, and a result is kept only when it is hosted on one of them (or, for
Indian BRSR filings, on the stock exchange with the company named in the title).
This is what stops other companies' pages from showing up.
"""
from __future__ import annotations

import json
import re
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
    qs = []
    for d in c["domains"][:2]:
        qs += [f"site:{d} sustainability report pdf", f"site:{d} ESG report", f"site:{d} annual report pdf"]
        if c["region"] == "India":
            qs.append(f"site:{d} business responsibility and sustainability report")
    if c["region"] == "India":
        qs.append(f'"{c["name"]}" BRSR pdf')
    return qs


def _ddgs_search(query: str, region: str) -> list:
    from ddgs import DDGS
    with DDGS() as search:
        return list(search.text(query, region=region, safesearch="moderate", max_results=10) or [])


def search_company_reports(company: str, search=_ddgs_search) -> dict:
    c = resolve(company)
    region = "in-en" if c["region"] == "India" else "wt-wt"
    seen, results = set(), []
    for q in queries_for(c):
        try:
            items = search(q, region)
        except Exception:  # noqa: BLE001 - one failed query must not stop the search
            continue
        for item in items:
            url = str(item.get("href", "")).strip()
            title = str(item.get("title", "")).strip()
            summary = str(item.get("body", "")).strip()
            dedupe_key = url.split("#")[0].split("?")[0].rstrip("/").lower()
            if not title or dedupe_key in seen or not accept(c, url, title, summary):
                continue
            seen.add(dedupe_key)
            results.append({
                "title": title,
                "year": report_year(f"{title} {url}") or "See document",
                "type": report_type(f"{title} {url}"),
                "url": url,
                "source": host_of(url),
                "summary": summary,
                "pdf": url.lower().split("?")[0].endswith(".pdf"),
                "_score": score(url, title, summary),
            })
    results.sort(key=lambda r: (r["_score"], r["year"]), reverse=True)
    for r in results:
        del r["_score"]
    return {
        "company": c["name"],
        "company_id": c["id"],
        "official_site": f"https://{c['domains'][0]}",
        "reports": results[:10],
        "count": min(len(results), 10),
    }
