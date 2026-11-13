"""Unit tests for the deterministic parts of Controversy Watch. No network, no LLM."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import controversy as cw

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()


def art(headline, days_ago=1, url=None, publisher="example.com"):
    ts = NOW - days_ago * cw.DAY
    return {"headline": headline, "url": url or f"https://example.com/{abs(hash(headline))}",
            "publisher": publisher, "ts": ts, "date": datetime.fromtimestamp(ts, timezone.utc).date().isoformat()}


def test_factor_weights_sum_to_one():
    assert sum(w for _, _, w in cw.FACTORS) == pytest.approx(1.0)


def test_severity_scales_with_evidence_stage():
    f = {k: 80 for k, _, _ in cw.FACTORS}
    assert cw.severity(f, "confirmed") == 80
    assert cw.severity(f, "allegation") == 64
    assert cw.severity(f, "resolved") == 44
    assert cw.severity(f, "allegation") < cw.severity(f, "investigation") < cw.severity(f, "confirmed")


def test_severity_tolerates_bad_input():
    assert cw.severity({"magnitude": "abc", "scale": None, "regulatory": 900}, "nonsense") == cw.clamp(0.18 * 100 * 0.8)


@pytest.mark.parametrize("score,expected", [(0, "Low"), (19, "Low"), (20, "Moderate"), (59, "Elevated"), (60, "High"), (80, "Critical")])
def test_bands(score, expected):
    assert cw.band(score) == expected


def test_risk_decays_with_a_45_day_half_life():
    fresh = [{"pillar": "E", "severity": 70, "last_ts": NOW}]
    old = [{"pillar": "E", "severity": 70, "last_ts": NOW - 45 * cw.DAY}]
    assert cw.exposure(old, NOW) == pytest.approx(cw.exposure(fresh, NOW) / 2)
    assert cw.risk_score(old, NOW) < cw.risk_score(fresh, NOW)


def test_risk_saturates_below_100_and_is_zero_without_incidents():
    many = [{"pillar": "G", "severity": 100, "last_ts": NOW} for _ in range(50)]
    assert cw.risk_score([], NOW) == 0
    assert cw.risk_score(many, NOW) == 100
    assert cw.risk_score(many, NOW, "E") == 0


def test_mentions_requires_whole_name():
    assert cw.mentions("SEBI issues notice to Vedanta over disclosures", "Vedanta Limited")
    assert cw.mentions("Reliance fined over effluent discharge", "Reliance Industries")
    assert not cw.mentions("ITCH festival opens in Kolkata", "ITC")
    assert not cw.mentions("Markets close higher", "Infosys")


def test_dedupe_drops_same_url_and_syndicated_headline():
    a = art("Plant fined for pollution", url="https://www.site.com/a/")
    b = art("Plant fined for pollution!", url="https://other.com/b")
    c = art("Different story", url="https://site.com/a")
    d = art("Third story", url="https://site.com/d")
    assert [x["headline"] for x in cw.dedupe([a, b, c, d])] == ["Plant fined for pollution", "Third story"]


def test_rule_classifier_reads_pillar_and_stage():
    r = cw.rule_classify("Pollution board fined Acme Rs 5 crore over effluent discharge")
    assert (r["pillar"], r["subcategory"], r["stage"], r["classifier"]) == ("E", "Pollution", "confirmed", "rules")
    assert cw.rule_classify("Regulator opens probe into alleged accounting fraud at Acme")["stage"] == "investigation"
    assert cw.rule_classify("Acme launches new phone, shares rise")["esg_relevant"] is False


def test_sanitise_rejects_values_outside_the_vocabulary():
    r = cw.sanitise({"esg_relevant": True, "pillar": "X", "subcategory": "made up", "stage": "guilty", "magnitude": 500})
    assert r["pillar"] == "G" and r["subcategory"] in cw.PILLARS["G"]
    assert r["stage"] == "allegation" and r["magnitude"] == 100


def test_llm_classify_uses_one_call_and_falls_back_per_row():
    calls = []

    def fake_llm(prompt):
        calls.append(prompt)
        return [{"i": 0, "esg_relevant": True, "about_company": True, "pillar": "S",
                 "subcategory": "Employee safety", "stage": "investigation", "magnitude": 70}]

    arts = [art("Worker injured at Acme plant"), art("Acme fined for pollution")]
    out = cw.llm_classify("Acme", arts, fake_llm)
    assert len(calls) == 1
    assert out[0]["classifier"] == "llm" and out[0]["subcategory"] == "Employee safety"
    assert out[1]["classifier"] == "rules" and out[1]["pillar"] == "E"


def classes_for(arts):
    return [cw.rule_classify(a["headline"]) for a in arts]


def test_incidents_group_within_30_days_and_recurrence_rises():
    arts = [
        art("Acme faces probe over effluent discharge", days_ago=40),
        art("Pollution board fined Acme over effluent discharge", days_ago=20),  # same incident, escalated
        art("Fresh toxic spill reported at Acme unit", days_ago=1, url="https://example.com/late"),
        art("Acme quarterly profit rises", days_ago=2),  # not ESG, dropped
    ]
    arts[2]["ts"] = NOW + 60 * cw.DAY  # more than 30 days after the last story: a new incident
    incs = cw.build_incidents(arts, classes_for(arts))
    assert len(incs) == 2
    first = next(i for i in incs if len(i["stories"]) == 2)
    second = next(i for i in incs if len(i["stories"]) == 1)
    assert first["stage"] == "confirmed"  # latest story sets the current stage
    assert second["stories"][0]["factors"]["recurrence"] > first["stories"][0]["factors"]["recurrence"]


def test_signals_flag_repeats_and_regulatory_matters():
    arts = [art("Acme faces probe over effluent discharge", days_ago=80),
            art("Toxic spill alleged at Acme plant", days_ago=5)]
    incs = cw.build_incidents(arts, classes_for(arts))
    types = {s["type"] for s in cw.signals(incs, NOW)}
    assert {"Repeated pattern", "Regulatory exposure"} <= types
    assert cw.signals([], NOW) == []


def test_say_do_flags_gap_when_report_is_silent():
    arts = [art("Customer data breach reported at Acme; regulator seeks report"),
            art("Worker injured at Acme plant, safety inquiry ordered")]
    incs = cw.build_incidents(arts, classes_for(arts))
    findings = {
        "Governance": [{"topic": "Data privacy", "status": "Not found"}],
        "Social": [{"topic": "Health & safety", "status": "Found", "confidence": "High", "page": 30, "value": "LTIFR 0.41"},
                   {"topic": "Workplace incidents", "status": "Not found"}],
    }
    rows = {r["sub"]: r for r in cw.say_do_check(findings, incs)}
    assert rows["Data privacy"]["flag"] == "Disclosure gap"
    assert rows["Employee safety"]["flag"] == "Compare claims"
    assert rows["Employee safety"]["topics"][0]["page"] == 30
    assert cw.say_do_check(findings, incs)[0]["flag"] == "Disclosure gap"  # gaps sort first


def test_low_confidence_disclosure_does_not_count_as_disclosed():
    arts = [art("Customer data breach reported at Acme")]
    incs = cw.build_incidents(arts, classes_for(arts))
    findings = {"Governance": [{"topic": "Data privacy", "status": "Found", "confidence": "Low"}]}
    assert cw.say_do_check(findings, incs)[0]["flag"] == "Disclosure gap"


def test_say_do_topics_exist_in_the_report_taxonomy():
    from backend.taxonomy import TOPICS
    known = {t for items in TOPICS.values() for t, _ in items}
    assert set(cw.SAY_DO) == {s for subs in cw.PILLARS.values() for s in subs}
    assert {t for ts in cw.SAY_DO.values() for t in ts} <= known


def test_collect_news_filters_parses_and_survives_a_dead_source():
    recent = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y%m%dT%H%M%SZ")
    stale = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y%m%dT%H%M%SZ")
    gdelt = json.dumps({"articles": [
        {"url": "https://a.com/1", "title": "Acme fined over pollution", "domain": "a.com", "seendate": recent},
        {"url": "https://a.com/2", "title": "Unrelated firm fined", "domain": "a.com", "seendate": recent},
        {"url": "https://a.com/3", "title": "Acme old story on pollution", "domain": "a.com", "seendate": stale},
    ]})

    def fake_get(url):
        if "gdeltproject" in url:
            return gdelt
        raise ConnectionError("down")

    arts, notes = cw.collect_news("Acme Ltd", 90, get=fake_get)
    assert [a["headline"] for a in arts] == ["Acme fined over pollution"]
    assert notes[0] == "GDELT: 3 results" and "unavailable" in notes[1]


def test_google_news_rss_parsing():
    when = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    xml = f"""<rss><channel><item><title>Acme &amp; partner face strike - The Daily</title>
<link>https://news.google.com/rss/articles/abc</link><pubDate>{when}</pubDate>
<source url="https://daily.example">The Daily</source></item></channel></rss>"""
    arts, _ = cw.collect_news("Acme", 30, get=lambda url: xml if "google" in url else "not json")
    assert arts[0]["headline"] == "Acme & partner face strike" and arts[0]["publisher"] == "The Daily"


def test_analyse_assembles_a_complete_result():
    arts = [art("Acme faces probe over effluent discharge", days_ago=3), art("Acme profit rises", days_ago=2)]
    out = cw.analyse("Acme", arts, classes_for(arts), findings=None, now=NOW)
    assert out["counts"] == {"articles": 2, "relevant": 1, "incidents": 1}
    assert 0 < out["risk"]["overall"] <= 100 and out["risk"]["S"] == 0
    assert out["say_do"] == []
    json.dumps(out)  # must be JSON serialisable for the API


def _feed(n_days_list):
    """Fake GDELT feed: one ESG headline per age in days."""
    arts = []
    for i, d in enumerate(n_days_list):
        seen = (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y%m%dT%H%M%SZ")
        arts.append({"url": f"https://n.com/{i}", "title": f"Regulator opens probe into Acme unit {i} over effluent discharge",
                     "domain": "n.com", "seendate": seen})
    body = json.dumps({"articles": arts})
    return lambda url: body if "gdeltproject" in url else "<rss></rss>"


def test_windows_are_nested_and_stories_classified_once():
    cw._MASTER.clear(); cw._CLASS.clear()
    get = _feed([2, 20, 45, 80, 120, 170])
    calls = []

    def llm(prompt):
        calls.append(prompt)
        n = prompt.count("\n") and len([l for l in prompt.split("HEADLINES:")[1].splitlines() if l.strip()])
        return [{"i": i, "esg_relevant": True, "about_company": True, "pillar": "E", "subcategory": "Pollution",
                 "stage": "investigation", "magnitude": 60, "scale": 50, "regulatory": 70, "reputational": 60,
                 "financial": 40, "source_credibility": 70, "confidence": 50} for i in range(n)]

    r30, r90, r180 = (cw.scan("Acme", d, llm_json=llm, get=get) for d in (30, 90, 180))
    assert len(calls) == 1  # one batch, classified once, reused by every window
    rel = [r["counts"]["relevant"] for r in (r30, r90, r180)]
    assert rel == [2, 4, 6]
    assert r30["windows"] == r90["windows"] == r180["windows"]
    assert r30["windows"]["90"]["relevant"] == r90["counts"]["relevant"]
    assert r30["risk"]["overall"] <= r90["risk"]["overall"] <= r180["risk"]["overall"]
    assert cw.scan("Acme", 90, llm_json=llm, get=get)["counts"] == r90["counts"]  # repeat scans agree
    assert r90["classifier"] == "llm"


def test_failed_llm_batch_falls_back_and_is_retried_later():
    cw._MASTER.clear(); cw._CLASS.clear()
    get = _feed([3, 10])

    def broken(prompt):
        raise TimeoutError

    r = cw.scan("Acme", 30, llm_json=broken, get=get)
    assert r["classifier"] == "rules" and any("will be retried" in n for n in r["sources"])
    ok = []
    cw.scan("Acme", 30, llm_json=lambda p: ok.append(1) or [], get=get)
    assert ok == [1]  # the stories were not cached as final, so the LLM is asked again
