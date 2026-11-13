"""Benchmark engine tests. Pure logic, no network."""
from backend import benchmark as bm
from backend.company_search import load_catalog, resolve
from backend.taxonomy import TOPICS

ALL = [t for items in TOPICS.values() for t, _ in items]


def F(**topics):
    """findings dict: topic=(status, confidence, value, page)"""
    out = {c: [] for c in TOPICS}
    for cat, items in TOPICS.items():
        for t, _ in items:
            key = t
            if key in topics:
                st, conf, val, pg = topics[key]
                out[cat].append({"topic": t, "status": st, "confidence": conf, "value": val, "page": pg})
            else:
                out[cat].append({"topic": t, "status": "Not found", "confidence": "", "value": "", "page": None})
    return out


def co(i, **topics):
    return {"id": f"c{i}", "name": f"Co {i}", "findings": F(**{k.replace("_", " "): v for k, v in topics.items()})}


def test_mappings_cover_the_taxonomy_and_catalog():
    assert len(ALL) == 30
    assert set(bm.FRAMEWORKS) == set(ALL)
    assert set(bm.KPIS) <= set(ALL) and set(bm.ABSOLUTE) <= set(ALL)
    for c in load_catalog():
        assert c["sector"] in bm.INDUSTRY, c["sector"]


def test_peers_share_the_group_and_prefer_region():
    infy = resolve("Infosys")
    peers = bm.peers_of(infy, load_catalog())
    assert [p["id"] for p in peers] == ["tcs", "wipro", "hcltech", "techm"]
    assert all(bm.industry_of(p) == "IT services" for p in bm.peers_of(resolve("Accenture"), load_catalog()))
    assert bm.peers_of(resolve("Accenture"), load_catalog())[-1]["region"] == "India"


def test_disclosed_needs_validated_evidence():
    assert bm.disclosed({"status": "Found", "confidence": "High"})
    assert not bm.disclosed({"status": "Found", "confidence": "Low"})
    assert not bm.disclosed({"status": "Inferred", "confidence": "Medium"})
    assert bm.mark({"status": "Found", "confidence": "Low"}) == "partial"
    assert bm.mark(None) == "missing"


def test_kpi_parsing():
    f = lambda v: {"status": "Found", "confidence": "High", "value": v}
    assert bm.parse_kpi("Renewable energy", f("34% of electricity")) == 34
    assert bm.parse_kpi("Board composition", f("4 of 9 directors independent")) == 44.4
    assert bm.parse_kpi("Board composition", f("Independent directors: 55%")) == 55
    assert bm.parse_kpi("Health & safety", f("LTIFR 0.41 per million hours")) == 0.41
    assert bm.parse_kpi("Health & safety", f("Zero fatalities")) is None
    assert bm.parse_kpi("Training", f("42.5 hours per employee")) == 42.5
    assert bm.parse_kpi("Renewable energy", f("Commitment to RE100")) is None
    assert bm.parse_kpi("Renewable energy", {"status": "Found", "confidence": "Low", "value": "90%"}) is None
    assert bm.parse_kpi("Water", f("10%")) is None


def test_positions_and_who_does_better():
    hi = ("Found", "High", "12,000 tCO2e", 20)
    target = co(0, Scope_1_emissions=hi, Biodiversity=hi)
    peers = [co(1, Scope_3_emissions=("Found", "High", "1.2 MtCO2e", 44), Scope_1_emissions=hi),
             co(2, Scope_3_emissions=("Found", "Medium", "900 ktCO2e", 51)),
             co(3), co(4, Scope_3_emissions=("Found", "High", "2 MtCO2e", 9))]
    out = bm.compare(target, peers)
    rows = {r["topic"]: r for r in out["topics"]}
    assert rows["Scope 3 emissions"]["position"] == "Lagging" and rows["Scope 3 emissions"]["peer_rate"] == 0.75
    assert rows["Biodiversity"]["position"] == "Leading"
    assert rows["Scope 1 emissions"]["position"] == "Leading"  # only 1 of 4 peers discloses
    assert rows["Water"]["position"] == "Industry gap"
    lag = out["brief"]["lagging"][0]
    assert lag["topic"] == "Scope 3 emissions" and [w["name"] for w in lag["who"]] == ["Co 1", "Co 2", "Co 4"]
    assert lag["who"][0]["page"] == 44
    assert out["brief"]["questions"][0].startswith("Will you disclose scope 3 emissions? 75% of peers do, for example Co 1")
    assert bm._lc("ESG governance") == "ESG governance" and bm._lc("Data privacy") == "data privacy"


def test_pillars_rank_and_median():
    hi = ("Found", "High", "x", 1)
    target = co(0, Water=hi)
    peers = [co(1, Water=hi, Waste=hi, Biodiversity=hi), co(2, Water=hi, Waste=hi), co(3)]
    p = bm.compare(target, peers)["pillars"]
    env = p["Environmental"]
    assert env["target"] == 8 and env["median"] == 17 and env["best"]["name"] == "Co 1"
    assert env["rank"] == 3 and env["of"] == 4
    assert p["Social"]["position"] == "In line"


def test_kpi_ranking_and_brief_notes():
    pct = lambda v: ("Found", "High", v, 5)
    target = co(0, Renewable_energy=pct("40% renewable electricity"), Health_and_safety=pct("LTIFR 0.30"))
    peers = [co(1, Renewable_energy=pct("80%")), co(2, Renewable_energy=pct("20%")),
             co(3, Health_and_safety=pct("LTIFR 0.12"))]
    target["findings"]["Social"] = [i | ({"status": "Found", "confidence": "High", "value": "LTIFR 0.30"} if i["topic"] == "Health & safety" else {}) for i in target["findings"]["Social"]]
    peers[2]["findings"]["Social"] = [i | ({"status": "Found", "confidence": "High", "value": "LTIFR 0.12"} if i["topic"] == "Health & safety" else {}) for i in peers[2]["findings"]["Social"]]
    out = bm.compare(target, peers)
    k = {x["topic"]: x for x in out["kpis"]}
    assert k["Renewable energy"]["leader"] == "Co 1" and k["Renewable energy"]["target_rank"] == 2
    assert k["Health & safety"]["leader"] == "Co 3" and k["Health & safety"]["target_rank"] == 2  # lower is better
    assert "Training" not in k  # nobody reported it
    assert any(n.startswith("Renewable share of energy or electricity: 40%, rank 2 of 3") for n in out["brief"]["kpi_notes"])


def test_frameworks_and_empty_peers():
    target = co(0, Scope_1_emissions=("Found", "High", "1", 1))
    out = bm.compare(target, [])
    assert out["frameworks"]["GRI"]["target"] > 0 and out["frameworks"]["GRI"]["peer_avg"] is None
    assert "305-3 Scope 3 emissions" in out["frameworks"]["GRI"]["missing"]
    assert out["brief"]["headline"].startswith("No peers")
    assert all(r["position"] == "No peers" for r in out["topics"])
