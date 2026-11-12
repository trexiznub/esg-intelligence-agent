"""Tests for the curated company report search. The web search is faked."""
import pytest

from backend import company_search as cs


def test_catalog_is_well_formed():
    cat = cs.load_catalog()
    assert len(cat) >= 50
    ids = [c["id"] for c in cat]
    assert len(ids) == len(set(ids))
    for c in cat:
        assert c["name"] and c["domains"] and c["region"] in {"India", "Global"}
        assert all("/" not in d and not d.startswith("www.") for d in c["domains"])


@pytest.mark.parametrize("q,expected", [("Infosys", "infosys"), ("infosys limited", "infosys"),
                                        ("TCS", "tcs"), ("l&t", "lt"), ("Google", "alphabet"), ("nestle", "nestle")])
def test_resolve_names_and_aliases(q, expected):
    assert cs.resolve(q)["id"] == expected


def test_unknown_company_is_rejected():
    with pytest.raises(cs.UnknownCompany):
        cs.resolve("Some Random Startup")


def test_only_official_domains_are_accepted():
    infy = cs.resolve("Infosys")
    assert cs.accept(infy, "https://www.infosys.com/sustainability/esg-report-2024-25.pdf", "Infosys ESG Report 2024-25", "")
    assert cs.accept(infy, "https://files.infosys.com/a/annual-report-2024.pdf", "Annual Report", "")
    # another company's site, a look-alike domain and an aggregator are all dropped
    assert not cs.accept(infy, "https://www.wipro.com/sustainability-report.pdf", "Wipro Sustainability Report", "")
    assert not cs.accept(infy, "https://infosys.com.evil.example/report.pdf", "Infosys sustainability report", "")
    assert not cs.accept(infy, "https://www.scribd.com/document/infosys-sustainability-report", "Infosys Sustainability Report", "")
    # official site but not a report
    assert not cs.accept(infy, "https://www.infosys.com/careers/sustainability-jobs.html", "Sustainability careers", "")
    assert not cs.accept(infy, "https://www.infosys.com/services/cloud.html", "Cloud services", "")
    assert not cs.accept(infy, "http://www.infosys.com/esg-report.pdf", "ESG report", "")


def test_exchange_filings_need_the_company_name():
    itc = cs.resolve("ITC")
    assert cs.accept(itc, "https://nsearchives.nseindia.com/corporate/ITC_BRSR_2025.pdf", "ITC Limited BRSR 2024-25", "")
    assert not cs.accept(itc, "https://nsearchives.nseindia.com/corporate/HUL_BRSR_2025.pdf", "Hindustan Unilever BRSR", "")


def test_year_and_type():
    assert cs.report_year("Integrated Annual Report FY2024-25") == "2024-25"
    assert cs.report_year("esg-report-2021.pdf and 2023 update") == "2023"
    assert cs.report_year("no year") == ""
    assert cs.report_type("Business Responsibility and Sustainability Report") == "BRSR"


def test_search_filters_ranks_and_dedupes():
    pages = [
        {"href": "https://www.tcs.com/content/dam/esg/tcs-esg-report-2024-25.pdf", "title": "TCS ESG Report 2024-25", "body": ""},
        {"href": "https://www.tcs.com/content/dam/esg/tcs-esg-report-2024-25.pdf?x=1", "title": "TCS ESG Report 2024-25", "body": ""},
        {"href": "https://www.tcs.com/investor-relations/annual-report-2022", "title": "Annual Report 2022", "body": ""},
        {"href": "https://www.infosys.com/esg-report.pdf", "title": "Infosys ESG Report", "body": ""},
        {"href": "https://www.linkedin.com/posts/tcs-esg", "title": "TCS ESG report", "body": ""},
    ]
    calls = []

    def fake(query, region):
        calls.append((query, region))
        return pages

    out = cs.search_company_reports("tcs", search=fake)
    assert out["company"] == "Tata Consultancy Services"
    assert [r["source"] for r in out["reports"]] == ["tcs.com", "tcs.com"]
    assert out["reports"][0]["year"] == "2024-25" and out["reports"][0]["pdf"] is True
    assert all(q.startswith(("site:tcs.com", '"Tata')) for q, _ in calls)
    assert calls[0][1] == "in-en"


def test_search_survives_failing_queries():
    def broken(query, region):
        raise RuntimeError("rate limited")

    out = cs.search_company_reports("Microsoft", search=broken)
    assert out["count"] == 0 and out["official_site"] == "https://microsoft.com"
