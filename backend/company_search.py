from urllib.parse import urlparse
from ddgs import DDGS


def search_company_reports(company: str):
    queries = [
        f'"{company}" sustainability report official',
        f'"{company}" ESG report PDF official',
        f'"{company}" annual report sustainability official',
        f'"{company}" BRSR report official',
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

        # Push obvious third-party aggregators down.
        if any(
            bad in url
            for bad in [
                "sustainabilityreports.com",
                "sustainabilityreports.org",
            ]
        ):
            score -= 5

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
