"""Crawler tests against a fake website. No network."""
from backend import site_crawler as sc

CO = {"id": "acme", "name": "Acme", "domains": ["acme.com"], "region": "India", "aliases": []}

HOME = """<html><head><title>Acme</title></head><body>
<a href="/careers">Careers</a><a href="/sustainability">Sustainability</a>
<a href="https://www.acme.com/investors/annual-reports">Annual reports</a>
<a href="https://other.com/esg">Partner ESG</a></body></html>"""
SUST = """<html><head><title>Sustainability | Acme</title></head><body>
<a href="/docs/Acme_ESG_Report_2024-25.pdf">Download</a>
<a href="/docs/csr-policy.pdf">CSR policy</a>
<a href="https://cdn.example.net/acme/brsr-fy25.pdf">BRSR FY2024-25</a>
<script>window.__DATA__={"file":"\\/docs\\/climate-report-2024.pdf"}</script></body></html>"""
INVESTORS = """<html><head><title>Annual reports | Acme</title></head><body>
<a href="annual-report-2023-24.pdf">Integrated Annual Report 2023-24</a>
<a href="https://acme.com/investors/q2-results-presentation.pdf">Q2 results presentation</a></body></html>"""
ROBOTS = "User-agent: *\nDisallow: /private\nSitemap: https://acme.com/sitemap.xml\n"
SITEMAP = """<urlset><url><loc>https://www.acme.com/sustainability</loc></url>
<url><loc>https://www.acme.com/private/esg-secret</loc></url>
<url><loc>https://www.acme.com/docs/sustainability-report-2022.pdf</loc></url>
<url><loc>https://www.acme.com/news/esg-award</loc></url></urlset>"""

SITE = {
    "https://acme.com/robots.txt": ("https://acme.com/robots.txt", "text/plain", ROBOTS),
    "https://acme.com/sitemap.xml": ("https://acme.com/sitemap.xml", "application/xml", SITEMAP),
    "https://acme.com/": ("https://www.acme.com/", "text/html", HOME),
    "https://acme.com/sustainability": ("https://www.acme.com/sustainability", "text/html", SUST),
    "https://www.acme.com/sustainability": ("https://www.acme.com/sustainability", "text/html", SUST),
    "https://www.acme.com/investors/annual-reports": ("https://www.acme.com/investors/annual-reports/", "text/html", INVESTORS),
    "https://acme.com/esg": ("https://login.evil.net/esg", "text/html", '<a href="/x/esg-report.pdf">ESG report</a>'),
}


def run(**kw):
    asked = []

    def fetch(url):
        asked.append(url)
        return SITE.get(url)

    return sc.crawl(CO, fetch=fetch, **kw), asked


def test_crawler_finds_report_pdfs_and_skips_policies():
    out, asked = run()
    urls = {p["url"] for p in out["pdfs"]}
    assert "https://www.acme.com/docs/Acme_ESG_Report_2024-25.pdf" in urls
    assert "https://cdn.example.net/acme/brsr-fy25.pdf" in urls  # CDN PDF linked from the official page
    assert "https://www.acme.com/docs/climate-report-2024.pdf" in urls  # embedded in script JSON
    assert "https://www.acme.com/investors/annual-reports/annual-report-2023-24.pdf" in urls  # relative link
    assert "https://www.acme.com/docs/sustainability-report-2022.pdf" in urls  # from the sitemap
    assert not any("policy" in u or "presentation" in u for u in urls)
    assert not any("evil" in u for u in urls)  # page redirected off the official site is ignored


def test_crawler_respects_robots_and_stays_on_domain():
    _, asked = run()
    assert not any("/private" in u for u in asked)
    assert not any("other.com" in u or "/careers" in u or "/news/" in u for u in asked)


def test_titles_prefer_meaningful_anchor_text():
    out, _ = run()
    by = {p["url"]: p["title"] for p in out["pdfs"]}
    assert by["https://cdn.example.net/acme/brsr-fy25.pdf"] == "BRSR FY2024-25"
    assert by["https://www.acme.com/docs/Acme_ESG_Report_2024-25.pdf"] == "Acme ESG Report 2024 25"


def test_crawler_reports_esg_pages_and_respects_page_budget():
    out, asked = run(max_pages=2)
    assert len([u for u in asked if not u.endswith((".txt", ".xml"))]) <= 2
    full, _ = run()
    assert any(p["title"] == "Sustainability | Acme" for p in full["pages"])


def test_dead_site_returns_empty_not_error():
    out = sc.crawl(CO, fetch=lambda u: None)
    assert out["pdfs"] == [] and out["notes"]


def test_link_extraction_handles_entities_and_junk():
    links = sc.extract_links('<a href="/a?x=1&amp;y=2">A</a><a href="mailto:x@y.z">m</a><a href="#top">t</a>', "https://acme.com/p/")
    assert links == [("https://acme.com/a?x=1&y=2", "A")]
