import json, math, os, re
import fitz

from backend.taxonomy import TOPICS
from backend.llm import llm_json
_Q = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"})

def fold(s):  # lowercase, straight quotes, collapsed whitespace
    return re.sub(r"\s+", " ", (s or "").translate(_Q)).strip().lower()

def tokens(s):
    return re.findall(r"[a-z0-9\-]{2,}", fold(s))


# ---------- 1. Document ingestion agent ----------
def extract_pdf(path, on_progress=lambda p: None):
    doc = fitz.open(path)
    pages, total = [], len(doc)
    for i, page in enumerate(doc, 1):
        pages.append({"n": i, "text": re.sub(r"\s+", " ", page.get_text("text")).strip()})
        on_progress(i / total)
    doc.close()
    return pages

def chunk_pages(pages, size=1200, overlap=200):
    chunks = []
    for p in pages:
        for i in range(0, max(len(p["text"]), 1), size - overlap):
            t = p["text"][i:i + size]
            if len(t) > 60:
                chunks.append({"page": p["n"], "text": t})
    return chunks

# ---------- 2. Retrieval layer (TF-IDF vector search) ----------
class Index:
    def __init__(self, chunks):
        self.chunks, self.df = chunks, {}
        for c in chunks:
            c["tf"] = {}
            for w in tokens(c["text"]): c["tf"][w] = c["tf"].get(w, 0) + 1
            for w in c["tf"]: self.df[w] = self.df.get(w, 0) + 1
    def retrieve(self, kw, k=3):
        q, n, scored = set(tokens(kw)), len(self.chunks), []
        for c in self.chunks:
            s = sum((1 + math.log(c["tf"][w])) * math.log(1 + n / self.df[w]) for w in q if w in c["tf"])
            if s > 0: scored.append((s, c))
        return [c for _, c in sorted(scored, key=lambda x: -x[0])[:k]]

# ---------- 3. ESG analysis agent ----------
def analyse_category(index, cat):
    topics = TOPICS[cat]
    seen = {}
    for _, kw in topics:
        for c in index.retrieve(kw, 3): seen[(c["page"], c["text"][:30])] = c
    ctx = "\n\n".join(f"[PAGE {c['page']}] {c['text']}" for c in list(seen.values())[:16])
    prompt = f"""
You are an ESG disclosure extraction agent.

Use ONLY the document excerpts below.
Never invent data, metrics, targets, years, or evidence.

For each topic, return exactly one JSON object with these fields:

{{
    "topic": "topic name",
    "status": "Disclosed" | "Inferred" | "Not found",
    "disclosure_status": "Disclosed" | "Not disclosed" | "Unclear",
    "metric_status": "Available" | "Not available" | "Not applicable" | "Unclear",
    "metric_type": "quantitative_performance" | "quantitative_target" | "qualitative_statement" | "combined_metric" | "none",
    "value": "exact figure or short disclosure summary",
    "year": "reporting year or target year if stated",
    "page": page number,
    "quote": "VERBATIM sentence copied from the excerpt supporting the finding"
}}

IMPORTANT INTERPRETATION RULES:

1. "Disclosed" means the topic is explicitly discussed in the document.

2. "Inferred" means the topic is only indirectly suggested.
   Do not treat an inference as a direct disclosure.

3. "Not found" means there is no reliable evidence for the topic in the provided excerpts.

4. A topic being disclosed does NOT automatically mean that a quantitative metric is available.

5. If the document discusses a topic but provides no numerical metric:
   disclosure_status = "Disclosed"
   metric_status = "Not available"
   metric_type = "qualitative_statement"

6. If an actual numerical current-period result is provided:
   metric_status = "Available"
   metric_type = "quantitative_performance"

7. If a numerical target/future ambition is provided:
   metric_status = "Available"
   metric_type = "quantitative_target"

8. If both a current performance figure and a target are mentioned,
   classify the primary finding according to the actual evidence and do not invent a value.

9. Combined metrics must remain combined.
   For example, if the document says "Scope 1 & 2 emissions decreased by 82%",
   DO NOT represent "-82%" as a standalone Scope 1 metric or a standalone Scope 2 metric.
   Treat it as a combined Scope 1 & 2 metric.

10. Do not convert a combined Scope 1 & 2 disclosure into separate Scope 1 and Scope 2 disclosures.

11. Do not assume that mentioning a topic means the document contains the underlying absolute metric.
    For example, discussing Scope 1 & 2 emissions does not prove that absolute tCO2e inventory data is disclosed.

12. "value" must contain only information actually supported by the evidence.

13. "quote" must be copied from the supplied excerpts.
    Do not paraphrase the quote.

        Return a JSON array, one object per topic, in this order:
        {', '.join([t for t, _ in topics])}

        EXCERPTS:
        {ctx}
    """

    r = llm_json(prompt)
    return r if isinstance(r, list) else r.get("items", [])

# ---------- 4. Validation agent (deterministic, no LLM) ----------
def validate(items, cat, pages):
    folded = [(p["n"], p["text"], fold(p["text"])) for p in pages]
    out = []

    def find_evidence(quote, page_hint=None):
        """
        Find source evidence even when the LLM quote contains
        ellipses or compressed sections.
        """
        raw_quote = str(quote or "").strip()

        if not raw_quote:
            return None

        # Normalize common ellipsis forms
        normalized_quote = (
            raw_quote
            .replace("…", "...")
            .replace("\r", " ")
            .replace("\n", " ")
        )

        # Split the quote into meaningful chunks around ellipses
        chunks = [
            fold(x).strip()
            for x in re.split(r"\.{3,}", normalized_quote)
            if fold(x).strip()
        ]

        # If there are no ellipses, try the original quote first
        if len(chunks) == 1:
            exact_q = chunks[0]

            preferred = [
                p for p in folded
                if page_hint is not None and p[0] == page_hint
            ]

            search_pages = preferred + [
                p for p in folded if p not in preferred
            ]

            for p in search_pages:
                if exact_q in p[2]:
                    start = p[2].find(exact_q)
                    return {
                        "page": p[0],
                        "context": p[1][max(0, start - 250): start + len(exact_q) + 250],
                        "matched": exact_q,
                    }

        # For ellipsized quotes, match meaningful chunks individually
        candidates = []

        preferred = [
            p for p in folded
            if page_hint is not None and p[0] == page_hint
        ]

        search_pages = preferred + [
            p for p in folded if p not in preferred
        ]

        for p in search_pages:
            matches = []

            for chunk in chunks:
                # Ignore extremely short fragments
                if len(chunk) < 8:
                    continue

                pos = p[2].find(chunk)

                if pos != -1:
                    matches.append((pos, chunk))

            # Require meaningful evidence:
            # - at least 2 chunks for an ellipsized quote, OR
            # - one substantial chunk (40+ characters)
            if len(matches) >= 2 or (
                len(matches) == 1 and len(matches[0][1]) >= 40
            ):
                matches.sort(key=lambda x: x[0])

                start = max(0, matches[0][0] - 250)
                end = min(
                    len(p[1]),
                    matches[-1][0] + len(matches[-1][1]) + 250
                )

                candidates.append({
                    "page": p[0],
                    "context": p[1][start:end],
                    "matched": " ... ".join(x[1] for x in matches),
                    "match_count": len(matches),
                })

        if candidates:
            # Prefer the page with the most independently matched chunks
            candidates.sort(
                key=lambda x: x.get("match_count", 0),
                reverse=True
            )
            return candidates[0]

        return None

    for topic, _ in TOPICS[cat]:
        it = next(
            (
                x for x in items
                if isinstance(x, dict)
                and fold(x.get("topic")) == fold(topic)
            ),
            None
        )

        if (
            not it
            or it.get("status") == "Not found"
            or not it.get("quote")
        ):
            out.append({
                "topic": topic,
                "status": "Not found",
                "value": "",
                "year": "",
                "page": None,
                "confidence": "",
                "quote": "",
                "context": "",
                "note": ""
            })
            continue

        evidence = find_evidence(
            it.get("quote"),
            it.get("page")
        )


        raw_status = str(it.get("status", "Inferred")).strip().lower()

        raw_status = str(it.get("status", "Inferred")).strip().lower()

        if raw_status in {"disclosed", "found", "directly identified", "supported"}:
            status = "Found"
        elif raw_status in {
            "requires further verification",
            "needs verification",
            "verify",
            "verification required",
        }:
            status = "Requires further verification"
        elif raw_status in {
            "not found",
            "missing",
            "not identified",
        }:
            status = "Not found"
        else:
            status = "Inferred"

        disclosure_status = str(
            it.get("disclosure_status", "")
        ).strip()

        metric_status = str(
            it.get("metric_status", "")
        ).strip()

        metric_type = str(
            it.get("metric_type", "none")
        ).strip()

        conf = "High"
        note = ""
        ctx = ""
        page = it.get("page")


        if not evidence:
            status = "Inferred"
            conf = "Low"
            note = (
                "Supporting evidence could not be reliably located "
                "in the source document; treat as unsupported."
            )

        else:
            page = evidence["page"]
            ctx = evidence["context"]

            # If the document directly discloses the topic and
            # supporting evidence was successfully validated, classify
            # the finding as Found rather than leaving the LLM status
            # as Inferred.
            disclosure_is_direct = disclosure_status.strip().lower() == "disclosed"

            if status == "Requires further verification":
                conf = "Medium"
            elif disclosure_is_direct:
                status = "Found"
            elif status == "Inferred":
                conf = "Low"
            else:
                status = "Found"

        if not evidence:
            status = "Inferred"
            conf = "Low"
            note = (
                "Supporting evidence could not be reliably located "
                "in the source document; treat as unsupported."
            )

        else:
            page = evidence["page"]
            ctx = evidence["context"]

            # Validate reported numeric values against the actual
            # source evidence rather than the LLM-generated quote.
            source_text = fold(ctx)

            nums = re.findall(
                r"\d[\d,]*(?:\.\d+)?",
                str(it.get("value", ""))
            )

            normalized_source = source_text.replace(",", "")

            if any(
                n.replace(",", "") not in normalized_source
                for n in nums
            ):
                conf = "Low"
                note = "Value not fully matched in source evidence."

            elif status == "Inferred":
                conf = "Low"

            elif not it.get("year"):
                conf = "Medium"

        out.append({
            "topic": topic,
            "status": status,
            "disclosure_status": disclosure_status,
            "metric_status": metric_status,
            "metric_type": metric_type,
            "value": it.get("value", ""),
            "year": it.get("year", ""),
            "page": page,
            "confidence": conf,
            "quote": it["quote"],
            "context": ctx,
            "note": note
        })

    return out

# ---------- 5. Gap analysis (rule-based) ----------
def find_gaps(findings):
    gaps = []

    for cat, items in findings.items():
        for item in items:
            status = item.get("status")
            confidence = item.get("confidence")

            if status == "Not found":
                gaps.append({
                    "cat": cat,
                    "topic": item["topic"],
                    "type": "disclosure_gap",
                    "severity": "watch",
                    "why": "Not identified in analysed document",
                })

            elif confidence == "Low":
                gaps.append({
                    "cat": cat,
                    "topic": item["topic"],
                    "type": "verification_required",
                    "severity": "watch",
                    "why": "Supporting evidence requires further verification",
                })

    return gaps

# ---------- 6. Report generation agent ----------
def generate_insights(findings):
    summ = "\n".join(f"{c}:\n" + "\n".join(f"- {x['topic']}: {x['status']}" + (f" = {x['value']}" if x['value'] else "") + (f" (p.{x['page']})" if x['page'] else "") for x in l) for c, l in findings.items())
    return llm_json(f"""You write cautious ESG analysis notes from VALIDATED findings only. Do not add facts. Use wording like "Based on the analysed disclosure...", "The report indicates...", "Not identified in the analysed document...", "Requires further verification...".
Return JSON: {{"highlights":[],"env":[],"social":[],"gov":[],"gaps":[],"risks":[],"investigate":[]}} with 2-4 short strings each.

FINDINGS:
{summ}""")

# ---------- Orchestrator (stage indices match the UI) ----------
def run(job, path):
    def stage(i, msg): job.update(stage=i, msg=msg)
    try:
        stage(0, "Document received")
        stage(1, "Extracting text...")
        pages = extract_pdf(path, lambda p: job.update(progress=p))
        chars = sum(len(p["text"]) for p in pages)
        if chars < 500 or chars / len(pages) < 150:
            raise ValueError(f"Little or no extractable text ({chars} characters across {len(pages)} pages). The PDF is probably scanned images; OCR is not supported.")
        stage(2, "Indexing document...")
        index = Index(chunk_pages(pages))
        meta = llm_json('From this report\'s first pages return JSON {"company","type","year"} using only the text; use "Not identified" if absent.\n\n' + " ".join(p["text"] for p in pages[:3])[:4000])
        raw = {}
        for cat in TOPICS:
            stage(3, f"Analysing ESG disclosures... ({cat})")
            raw[cat] = analyse_category(index, cat)
        stage(4, "Validating findings...")
        findings = {c: validate(raw[c], c, pages) for c in raw}
        stage(5, "Identifying gaps...")
        gaps = find_gaps(findings)
        stage(6, "Generating insights...")
        ins = generate_insights(findings)
        ins["gaps"] = gaps
        job.update(status="done", stage=7, msg="Analysis complete",
                   result={"meta": {"company": meta.get("company"), "type": meta.get("type"), "year": meta.get("year"), "pages": len(pages)}, "f": findings, "ins": ins})
    except Exception as e:  # surfaced to the UI, never swallowed
        job.update(status="error", error=str(e))
    finally:
        try: os.remove(path)
        except OSError: pass
