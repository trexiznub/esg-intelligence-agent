"""Peer benchmarking: compares one company's validated findings with its industry peers.

Every company in a benchmark is analysed by the same pipeline (same 30 topics, same
retrieval, same quote validation), so the comparison is like-for-like on scope.
Everything here is pure, deterministic code over those results.

Comparisons are about *disclosure* (does the report disclose a topic, with evidence
that survived validation?) and, for a few metrics with comparable units, about the
reported value. Absolute figures such as tonnes of CO2e depend on company size and
are shown, not ranked.
"""
from __future__ import annotations

import re
from statistics import median

from backend.taxonomy import TOPICS

# Sector (companies.json) -> peer group.
INDUSTRY = {
    "IT Services": "IT services",
    "Technology": "Technology & telecom", "Semiconductors": "Technology & telecom", "Telecom": "Technology & telecom",
    "Technology & Retail": "Retail & e-commerce", "Retail": "Retail & e-commerce",
    "Banking": "Banking & financial services",
    "FMCG": "Consumer goods", "Consumer Goods": "Consumer goods", "Food & Beverage": "Consumer goods",
    "Beverages": "Consumer goods", "Consumer": "Consumer goods", "Paints": "Consumer goods", "Apparel": "Consumer goods",
    "Automobiles": "Automobiles",
    "Steel": "Metals & mining", "Metals & Mining": "Metals & mining", "Metals": "Metals & mining", "Mining": "Metals & mining",
    "Energy & Conglomerate": "Energy & utilities", "Oil & Gas": "Energy & utilities", "Power": "Energy & utilities",
    "Renewable Energy": "Energy & utilities",
    "Engineering": "Industrials & infrastructure", "Infrastructure": "Industrials & infrastructure",
    "Industrials": "Industrials & infrastructure", "Cement": "Industrials & infrastructure",
    "Pharmaceuticals": "Healthcare & pharma", "Health Care": "Healthcare & pharma",
}

# Indicative mapping of each taxonomy topic to reporting frameworks.
# GRI = GRI Standards disclosure, BRSR = SEBI BRSR principle/section, TCFD = TCFD pillar.
FRAMEWORKS = {
    "Scope 1 emissions": {"GRI": "305-1", "BRSR": "Principle 6", "TCFD": "Metrics & targets"},
    "Scope 2 emissions": {"GRI": "305-2", "BRSR": "Principle 6", "TCFD": "Metrics & targets"},
    "Scope 3 emissions": {"GRI": "305-3", "BRSR": "Principle 6", "TCFD": "Metrics & targets"},
    "Energy consumption": {"GRI": "302-1", "BRSR": "Principle 6"},
    "Renewable energy": {"GRI": "302-1", "BRSR": "Principle 6"},
    "GHG emissions (total)": {"GRI": "305-4", "BRSR": "Principle 6", "TCFD": "Metrics & targets"},
    "Water": {"GRI": "303-3", "BRSR": "Principle 6"},
    "Waste": {"GRI": "306-3", "BRSR": "Principle 6"},
    "Biodiversity": {"GRI": "304-2", "BRSR": "Principle 6"},
    "Climate targets": {"GRI": "305-5", "BRSR": "Principle 6", "TCFD": "Metrics & targets"},
    "Net-zero commitment": {"BRSR": "Principle 6", "TCFD": "Strategy"},
    "Emissions reduction initiatives": {"GRI": "305-5", "BRSR": "Principle 6", "TCFD": "Strategy"},
    "Employee diversity": {"GRI": "405-1", "BRSR": "Section A"},
    "Health & safety": {"GRI": "403-9", "BRSR": "Principle 3"},
    "Training": {"GRI": "404-1", "BRSR": "Principle 3"},
    "Labour practices": {"GRI": "401-1", "BRSR": "Principle 3"},
    "Human rights": {"GRI": "2-23", "BRSR": "Principle 5"},
    "Community initiatives": {"GRI": "413-1", "BRSR": "Principle 8"},
    "Employee engagement": {"BRSR": "Principle 3"},
    "Workplace incidents": {"GRI": "403-9", "BRSR": "Principle 3"},
    "Social impact": {"GRI": "413-1", "BRSR": "Principle 8"},
    "Board composition": {"GRI": "2-9", "BRSR": "Section B", "TCFD": "Governance"},
    "Ethics": {"GRI": "2-23", "BRSR": "Principle 1"},
    "Anti-corruption": {"GRI": "205-2", "BRSR": "Principle 1"},
    "Risk management": {"GRI": "2-12", "BRSR": "Section A", "TCFD": "Risk management"},
    "Data privacy": {"GRI": "418-1", "BRSR": "Principle 9"},
    "Whistleblower mechanism": {"GRI": "2-26", "BRSR": "Principle 1"},
    "ESG governance": {"GRI": "2-13", "BRSR": "Section B", "TCFD": "Governance"},
    "Sustainability oversight": {"GRI": "2-12", "BRSR": "Section B", "TCFD": "Governance"},
    "Policies": {"GRI": "2-23", "BRSR": "Section B"},
}

# Metrics that can be compared across companies of different size.
# topic -> (label, unit, higher_is_better, parser name)
KPIS = {
    "Renewable energy": ("Renewable share of energy or electricity", "%", True, "percent"),
    "Employee diversity": ("Women in the workforce", "%", True, "percent"),
    "Board composition": ("Independent directors on the board", "%", True, "share"),
    "Health & safety": ("Lost-time injury frequency rate", "LTIFR", False, "ltifr"),
    "Training": ("Training hours per employee", "hours", True, "hours"),
}
ABSOLUTE = ["Scope 1 emissions", "Scope 2 emissions", "Scope 3 emissions", "Energy consumption", "Water", "Waste"]

CATS = list(TOPICS)
_NUM = r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"


def industry_of(company: dict) -> str:
    return INDUSTRY.get(company.get("sector", ""), company.get("sector", "Other"))


def peers_of(company: dict, catalog: list, n: int = 4) -> list:
    """Same peer group; same region first, then the rest, catalogue order within each."""
    group = industry_of(company)
    same = [c for c in catalog if c["id"] != company["id"] and industry_of(c) == group]
    same.sort(key=lambda c: c["region"] != company["region"])
    return same[:n]


def disclosed(item: dict | None) -> bool:
    """Disclosed = found with evidence that survived validation at Medium or High confidence."""
    return bool(item) and item.get("status") == "Found" and item.get("confidence") in ("High", "Medium")


def mark(item: dict | None) -> str:
    if disclosed(item):
        return "disclosed"
    if item and item.get("status") in ("Found", "Inferred", "Requires further verification"):
        return "partial"
    return "missing"


def _index(findings: dict) -> dict:
    return {i.get("topic"): i for items in (findings or {}).values() for i in items if isinstance(i, dict)}


# ---------- KPI parsing ----------
def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse_kpi(topic: str, item: dict | None):
    """Extracts a comparable number from a validated finding, or None."""
    if topic not in KPIS or not disclosed(item):
        return None
    kind = KPIS[topic][3]
    text = f"{item.get('value', '')}".lower()
    if kind in ("percent", "share"):
        m = re.search(_NUM + r"\s*%", text)
        if m and 0 <= _num(m.group(1)) <= 100:
            return round(_num(m.group(1)), 2)
        if kind == "share":
            m = re.search(r"(\d+)\s*(?:of|out of|/)\s*(\d+)", text)
            if m and 0 < int(m.group(1)) <= int(m.group(2)) <= 40:
                return round(100 * int(m.group(1)) / int(m.group(2)), 1)
        return None
    if kind == "ltifr":
        if "ltifr" not in text and "lost time" not in text and "lost-time" not in text:
            return None
        m = re.search(_NUM, text.split("ltifr")[-1] if "ltifr" in text else text)
        return round(_num(m.group(1)), 3) if m and _num(m.group(1)) < 50 else None
    if kind == "hours":
        m = re.search(_NUM + r"\s*(?:hours|hrs|hour)", text)
        return round(_num(m.group(1)), 1) if m and _num(m.group(1)) < 500 else None
    return None


# ---------- comparison ----------
def _position(target_ok: bool, rate: float, n_peers: int) -> str:
    if not n_peers:
        return "No peers"
    if target_ok:
        return "Leading" if rate < 0.5 else "In line"
    if rate >= 0.5:
        return "Lagging"
    return "Industry gap" if rate < 0.25 else "Behind some peers"


def compare(target: dict, peers: list) -> dict:
    """target / peers: {"id", "name", "findings", "meta"?}. Returns the full benchmark."""
    t_idx = _index(target["findings"])
    p_idx = [(p, _index(p["findings"])) for p in peers]
    n = len(peers)

    topics = []
    for cat, items in TOPICS.items():
        for topic, _ in items:
            t_item = t_idx.get(topic)
            cells = []
            for p, idx in p_idx:
                it = idx.get(topic)
                cells.append({"id": p["id"], "name": p["name"], "mark": mark(it),
                              "value": (it or {}).get("value", "") if disclosed(it) else "",
                              "page": (it or {}).get("page") if disclosed(it) else None})
            rate = sum(c["mark"] == "disclosed" for c in cells) / n if n else 0.0
            topics.append({
                "topic": topic, "cat": cat, "frameworks": FRAMEWORKS.get(topic, {}),
                "target": {"mark": mark(t_item), "value": (t_item or {}).get("value", ""),
                           "page": (t_item or {}).get("page")},
                "peers": cells, "peer_rate": round(rate, 2),
                "position": _position(disclosed(t_item), rate, n),
                "leaders": [c["name"] for c in cells if c["mark"] == "disclosed"],
            })

    def coverage(idx, cat=None):
        sel = [t for c, items in TOPICS.items() if cat in (None, c) for t, _ in items]
        return round(100 * sum(disclosed(idx.get(t)) for t in sel) / len(sel))

    pillars = {}
    for cat in [*CATS, None]:
        t = coverage(t_idx, cat)
        ps = [(p["name"], coverage(idx, cat)) for p, idx in p_idx]
        med = round(median(v for _, v in ps)) if ps else None
        best = max(ps, key=lambda x: x[1]) if ps else None
        rank = 1 + sum(v > t for _, v in ps)
        pos = ("No peers" if not ps else "Leading" if t >= med + 10 else "Lagging" if t <= med - 10 else "In line")
        pillars[cat or "Overall"] = {"target": t, "median": med, "best": {"name": best[0], "value": best[1]} if best else None,
                                     "peers": [{"name": nm, "value": v} for nm, v in ps],
                                     "rank": rank, "of": n + 1, "position": pos}

    kpis = []
    for topic, (label, unit, higher, _) in KPIS.items():
        rows = [{"name": target["name"], "target": True, "value": parse_kpi(topic, t_idx.get(topic)),
                 "page": (t_idx.get(topic) or {}).get("page")}]
        rows += [{"name": p["name"], "target": False, "value": parse_kpi(topic, idx.get(topic)),
                  "page": (idx.get(topic) or {}).get("page")} for p, idx in p_idx]
        known = [r for r in rows if r["value"] is not None]
        if len(known) < 2:
            continue
        known.sort(key=lambda r: r["value"], reverse=higher)
        for i, r in enumerate(known):
            r["rank"] = i + 1
        t_row = rows[0]
        peer_vals = [r["value"] for r in rows[1:] if r["value"] is not None]
        kpis.append({"topic": topic, "label": label, "unit": unit, "higher_is_better": higher, "rows": rows,
                     "leader": known[0]["name"], "peer_median": round(median(peer_vals), 2) if peer_vals else None,
                     "target_rank": t_row.get("rank"), "ranked": len(known)})

    absolute = [{"topic": t, "rows": [{"name": target["name"], "value": (t_idx.get(t) or {}).get("value", "") if disclosed(t_idx.get(t)) else ""}]
                 + [{"name": p["name"], "value": (idx.get(t) or {}).get("value", "") if disclosed(idx.get(t)) else ""} for p, idx in p_idx]}
                for t in ABSOLUTE]

    frameworks = {}
    for fw in ("GRI", "BRSR", "TCFD"):
        mapped = [r for r in topics if fw in r["frameworks"]]
        groups = {}
        for r in mapped:
            groups.setdefault(r["frameworks"][fw], []).append(r)
        t_cov = round(100 * sum(r["target"]["mark"] == "disclosed" for r in mapped) / len(mapped))
        p_cov = [round(100 * sum(r["peers"][i]["mark"] == "disclosed" for r in mapped) / len(mapped)) for i in range(n)]
        frameworks[fw] = {
            "target": t_cov, "peer_avg": round(sum(p_cov) / n) if n else None,
            "missing": sorted({r["frameworks"][fw] + " " + r["topic"] for r in mapped if r["target"]["mark"] != "disclosed"}),
            "sections": [{"ref": ref, "topics": [r["topic"] for r in rs],
                          "target": sum(r["target"]["mark"] == "disclosed" for r in rs), "total": len(rs)}
                         for ref, rs in sorted(groups.items())],
        }

    return {"target": {"id": target["id"], "name": target["name"]},
            "peers": [{"id": p["id"], "name": p["name"], "meta": p.get("meta", {})} for p in peers],
            "topics": topics, "pillars": pillars, "kpis": kpis, "absolute": absolute,
            "frameworks": frameworks, "brief": brief(target["name"], topics, pillars, kpis, n)}


def _lc(topic: str) -> str:
    """Lower-cases a topic for use mid-sentence, keeping acronyms such as ESG."""
    return topic if topic[:2].isupper() else topic[:1].lower() + topic[1:]


def fmt(v, unit: str) -> str:
    return f"{v:g}%" if unit == "%" else f"{v:g}" if unit == "LTIFR" else f"{v:g} {unit}"


def brief(name: str, topics: list, pillars: dict, kpis: list, n: int) -> dict:
    """Rule-based analyst summary. Every line is traceable to the rows above."""
    if not n:
        return {"headline": "No peers have been analysed yet.", "lagging": [], "leading": [], "kpi_notes": [], "questions": []}
    o = pillars["Overall"]
    pos = {"Leading": "ahead of", "Lagging": "behind", "In line": "in line with"}[o["position"]]
    headline = (f"{name} discloses {o['target']}% of the 30 tracked topics with validated evidence, "
                f"{pos} the peer median of {o['median']}% (rank {o['rank']} of {o['of']}).")
    weakest = min((c for c in CATS), key=lambda c: pillars[c]["target"] - pillars[c]["median"])
    w = pillars[weakest]
    if w["target"] < w["median"]:
        headline += f" The widest gap is in {weakest.lower()} ({w['target']}% against {w['median']}%)."

    lag = sorted((r for r in topics if r["position"] == "Lagging"), key=lambda r: -r["peer_rate"])
    lead = sorted((r for r in topics if r["position"] == "Leading"), key=lambda r: r["peer_rate"])
    lagging = [{"topic": r["topic"], "cat": r["cat"], "peer_rate": r["peer_rate"],
                "who": [{"name": c["name"], "value": c["value"], "page": c["page"]} for c in r["peers"] if c["mark"] == "disclosed"]}
               for r in lag]
    leading = [{"topic": r["topic"], "cat": r["cat"], "peer_rate": r["peer_rate"], "value": r["target"]["value"],
                "page": r["target"]["page"]} for r in lead]

    kpi_notes = []
    for k in kpis:
        t = k["rows"][0]
        if t["value"] is None:
            continue
        better = "higher" if k["higher_is_better"] else "lower"
        kpi_notes.append(f"{k['label']}: {fmt(t['value'], k['unit'])}, rank {k['target_rank']} of {k['ranked']} "
                         f"({better} is better; leader {k['leader']}).")

    questions = []
    for r in lag[:5]:
        ex = next((c for c in r["peers"] if c["mark"] == "disclosed"), None)
        q = f"Will you disclose {_lc(r['topic'])}? {round(100 * r['peer_rate'])}% of peers do"
        questions.append(q + (f", for example {ex['name']}." if ex else "."))
    for r in topics:
        if r["target"]["mark"] == "partial" and r["peer_rate"] >= 0.5 and len(questions) < 7:
            questions.append(f"Can you provide verifiable data for {_lc(r['topic'])}? The report mentions it, "
                             "but the evidence did not pass validation.")
    return {"headline": headline, "lagging": lagging, "leading": leading, "kpi_notes": kpi_notes, "questions": questions}
