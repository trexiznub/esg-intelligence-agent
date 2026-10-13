from urllib.parse import urlparse
from ddgs import DDGS


def search_company_reports(company: str):
    queries = [
        f'"{company}" sustainability report PDF',
        f'"{company}" ESG report PDF',
        f'"{company}" annual report PDF',
        f'"{company}" integrated report PDF',
        f'"{company}" climate report PDF',
        f'"{company}" BRSR PDF',
    ]

    results = []
    seen_urls = set()

    company_words = [
        word.lower()
        for word in company.replace(",", " ").split()
        if len(word) > 2
    ]

    def score(item):
        url = item["url"].lower()
        text = (item["title"] + " " + item["summary"]).lower()

        score = 0

        if any(x in url for x in [".com", ".org"]):
            score += 1

        if "sustainability" in text:
            score += 4

        if "esg" in text:
            score += 3

        if "brsr" in text:
            score += 3

        if "annual report" in text:
            score += 2

        if "download" in url or ".pdf" in url:
            score += 2

        if any(word in url for word in company_words):
            score += 5

        # Strongly penalize irrelevant sources.
        bad_terms = [
            "linkedin.com",
            "wikipedia.org",
            "jobs.",
            "careers.",
            "/jobs",
            "/careers",
            "indeed.com",
            "glassdoor.",
            "facebook.com",
            "instagram.com",
            "youtube.com",
            "news.",
        ]

        if any(bad in url for bad in bad_terms):
            score -= 20

        bad_text_terms = [
            "job opening",
            "job openings",
            "careers",
            "hiring",
            "truck driving",
            "vacancy",
            "recruitment",
            "linkedin",
            "wikipedia",
        ]

        if any(term in text for term in bad_text_terms):
            score -= 15

        # Strong boost for actual ESG/report documents.
        report_terms = [
            "sustainability report",
            "esg report",
            "annual report",
            "integrated report",
            "climate report",
            "brsr",
            "responsible business",
            "sustainability performance",
        ]

        if any(term in text for term in report_terms):
            score += 8

        # Prefer direct PDFs and report/download pages.
        if ".pdf" in url or "download" in url:
            score += 4

        return score

    with DDGS() as search:
        for query in queries:
            try:
                items = search.text(
                    query,
                    region="in-en",
                    safesearch="moderate",
                    max_results=8,
                )
            except Exception:
                continue

            for item in items:
                url = str(item.get("href", "")).strip()
                title = str(item.get("title", "")).strip()
                summary = str(item.get("body", "")).strip()

                if not url.startswith("https://"):
                    continue

                if url in seen_urls or not title:
                    continue

                seen_urls.add(url)

                text = f"{title} {summary}".lower()

                if "sustainability" in text:
                    report_type = "Sustainability / ESG Report"
                elif "esg" in text:
                    report_type = "ESG Report"
                elif "brsr" in text:
                    report_type = "BRSR Report"
                elif "climate" in text:
                    report_type = "Climate Report"
                elif "annual" in text or "integrated" in text:
                    report_type = "Annual / Integrated Report"
                else:
                    report_type = "Company Report"

                results.append({
                    "title": title,
                    "year": "See publication",
                    "type": report_type,
                    "url": url,
                    "source": urlparse(url).netloc,
                    "summary": summary,
                })

    results.sort(key=score, reverse=True)

    return {
        "company": company,
        "reports": results[:10],
        "count": min(len(results), 10),
    }
