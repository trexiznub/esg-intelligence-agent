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
    prompt = f"""You are an ESG disclosure extraction agent. Use ONLY the excerpts below. Never invent data or use outside knowledge.
For each topic return an object: {{"topic","status":"Disclosed"|"Inferred"|"Not found","value":"exact figure or short disclosure summary","year":"reporting year if stated","page":number,"quote":"VERBATIM sentence copied from the excerpt supporting the value (max 250 chars)"}}.
"Disclosed"=explicit in text. "Inferred"=only indirectly implied. "Not found"=absent (empty value and quote, page null).
Return a JSON array, one object per topic, in this order: {'; '.join(t for t, _ in topics)}

EXCERPTS:
{ctx}"""
    r = llm_json(prompt)
    return r if isinstance(r, list) else r.get("items", [])

# ---------- 4. Validation agent (deterministic, no LLM) ----------
def validate(items, cat, pages):
    folded = [(p["n"], p["text"], fold(p["text"])) for p in pages]
    out = []
    for topic, _ in TOPICS[cat]:
        it = next((x for x in items if isinstance(x, dict) and fold(x.get("topic")) == fold(topic)), None)
        if not it or it.get("status") == "Not found" or not it.get("quote"):
            out.append({"topic": topic, "status": "Not found", "value": "", "year": "", "page": None, "confidence": "", "quote": "", "context": "", "note": ""})
            continue
        q = fold(it["quote"])
        hit = next((p for p in folded if p[0] == it.get("page") and q in p[2]), None) or next((p for p in folded if q in p[2]), None)
        status, conf, note, ctx, page = it.get("status", "Inferred"), "High", "", "", it.get("page")
        if not hit:
            status, conf, note = "Inferred", "Low", "Quote not found verbatim in document; treat as unsupported."
        else:
            page = hit[0]
            i = hit[2].find(q)
            ctx = q[:300]
            nums = [n.replace(",", "") for n in re.findall(r"\d[\d,\.]*", str(it.get("value", "")))]
            if any(n not in q.replace(",", "") for n in nums):
                conf, note = "Low", "Value not fully matched in quote."
            elif status == "Inferred": conf = "Low"
            elif not it.get("year"): conf = "Medium"
        out.append({"topic": topic, "status": status, "value": it.get("value", ""), "year": it.get("year", ""), "page": page, "confidence": conf, "quote": it["quote"], "context": ctx, "note": note})
    return out

# ---------- 5. Gap analysis (rule-based) ----------
def find_gaps(findings):
    return [{"cat": c, "topic": x["topic"], "why": "Not identified in analysed document" if x["status"] == "Not found" else "Weakly supported; requires further verification"}
            for c, l in findings.items() for x in l if x["status"] == "Not found" or x["confidence"] == "Low"]

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
        find_gaps(findings)
        stage(6, "Generating insights...")
        ins = generate_insights(findings)
        job.update(status="done", stage=7, msg="Analysis complete",
                   result={"meta": {"company": meta.get("company"), "type": meta.get("type"), "year": meta.get("year"), "pages": len(pages)}, "f": findings, "ins": ins})
    except Exception as e:  # surfaced to the UI, never swallowed
        job.update(status="error", error=str(e))
    finally:
        try: os.remove(path)
        except OSError: pass
