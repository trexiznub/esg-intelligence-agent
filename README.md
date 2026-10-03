# ESG Intelligence Agent

An AI-assisted ESG disclosure analyser. Upload a sustainability report, annual report or BRSR (PDF) and get a source-traceable dashboard of ESG metrics, disclosure gaps and cautious insights. It is a research prototype, **not** an ESG rating, assurance opinion, investment recommendation or compliance determination.

## Architecture
```
Browser (frontend/index.html)  --upload-->  FastAPI (backend/main.py)
        ^                                        |  background thread
        |  polls /api/jobs/{id}                  v
        +------ real stage status ----- pipeline.py
                                         1 Ingestion  (PyMuPDF, page numbers kept, chunking)
                                         2 Retrieval  (TF-IDF vector search per ESG topic)
                                         3 ESG analysis (LLM, retrieved excerpts only)
                                         4 Validation (deterministic: quote must exist in the PDF)
                                         5 Gap analysis (rule-based)
                                         6 Report/insights (LLM, validated findings only)
```
The agents are logical stages sharing one LLM. The UI stage tracker reads the backend's actual stage, nothing is simulated.

## Tech stack
Python, FastAPI, PyMuPDF, Anthropic API, vanilla JS/CSS frontend (no build step).

## Setup
```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
# edit .env and add your ANTHROPIC_API_KEY
uvicorn main:app --port 8000
```
Open http://localhost:8000. The **View Demo** button works without an API key and uses clearly labelled mock data.

## Environment variables
| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Required for real analysis. Server-side only, never sent to the browser. |
| `ANTHROPIC_MODEL` | Optional model override (default `claude-sonnet-4-6`). |

## How the agentic workflow works
1. **Ingestion** extracts text per page and chunks it (1200 chars, 200 overlap) keeping page numbers.
2. **Retrieval** scores chunks against keywords for each topic (e.g. Scope 1) and passes only the top excerpts to the model.
3. **Analysis** asks the model for value, year, page and a *verbatim quote* per topic, or "Not found".
4. **Validation** checks in plain code that the quote appears in the PDF (correcting the page if needed) and that numbers in the value appear in the quote. Failures become *Inferred* with low confidence.
5. **Gap analysis** lists topics that are Not found or low confidence.
6. **Insights** are written only from validated findings using cautious wording.

## Known limitations
- No OCR: scanned PDFs are rejected with a clear message.
- Tables may extract imperfectly; figures inside tables can be missed.
- Retrieval is keyword-based (TF-IDF), not embeddings; very differently worded disclosures can be missed.
- Validation proves a quote exists, not that the model interpreted it correctly. Always verify against the source.
- Jobs are held in memory and lost on restart; single-user prototype with no authentication.
- 25 MB upload limit.

## Example interview explanation
"I built an agentic pipeline instead of a chatbot. Retrieval narrows a long report to relevant passages, the model extracts metrics with verbatim quotes, and a separate non-AI validation step checks each quote against the PDF before anything is shown. Unsupported claims are downgraded, missing topics are reported as gaps rather than guessed, and every figure links to its page, so an analyst can audit the output. I deliberately avoided a made-up ESG score and I state the limits, such as no OCR and keyword retrieval, openly."
