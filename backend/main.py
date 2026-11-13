"""FastAPI app: report analysis jobs, company report search and Controversy Watch.

Run from the repository root:  uvicorn backend.main:app --port 8000
"""
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from backend import benchmark, controversy, peers, pipeline
from backend.company_search import UnknownCompany, load_catalog, search_company_reports
from backend.report import generate_report

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(Path(__file__).parent / ".env")

app = FastAPI(title="ESG Intelligence Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5501",
        "http://localhost:5501",
        "https://trexiznub.github.io",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

MAX_MB = 100
MAX_JOBS = 50          # finished jobs kept in memory
JOBS: dict = {}


def _has_key() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def _start_job(data: bytes, msg: str, company=None, source_url: str = "") -> dict:
    """Validates a PDF, saves it to a temp file and starts the pipeline thread."""
    if len(data) > MAX_MB * 1048576:
        raise HTTPException(413, f"File exceeds {MAX_MB} MB.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Only valid PDF files are supported.")

    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.write(fd, data)
    os.close(fd)

    for old in list(JOBS)[:-MAX_JOBS]:  # dicts keep insertion order: drop the oldest
        if JOBS[old]["status"] != "running":
            del JOBS[old]

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status": "running", "stage": 0, "msg": msg, "progress": 0, "result": None, "error": None,
                    "company_id": company["id"] if company else None}
    threading.Thread(target=_run_job, args=(JOBS[job_id], path, company, source_url), daemon=True).start()
    return {"job_id": job_id}


def _run_job(job: dict, path: str, company, source_url: str) -> None:
    """Runs the pipeline, then files the result in the peer store so later benchmarks can use it."""
    pipeline.run(job, path)
    if job["status"] != "done":
        return
    company = company or peers.detect_company(str(job["result"].get("meta", {}).get("company", "")))
    if company:
        job["company_id"] = company["id"]
        job["result"]["company_id"] = company["id"]
        peers.put(company, job["result"], source_url)


@app.get("/api/health")
def health():
    return {"ok": True, "api_key_configured": _has_key()}


@app.post("/api/company-search")
def company_search(payload: dict):
    company = str(payload.get("company", "")).strip()
    if not company:
        raise HTTPException(400, "Enter a company name.")
    if len(company) > 150:
        raise HTTPException(400, "Company name is too long.")
    try:
        return search_company_reports(company)
    except UnknownCompany as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Company search failed: {str(e)[:300]}")


@app.post("/api/analyse")
async def analyse(file: UploadFile = File(...)):
    if not _has_key():
        raise HTTPException(500, "GEMINI_API_KEY is not set. Add it to backend/.env and restart.")
    return _start_job(await file.read(), "Document received")


@app.post("/api/analyse-url")
def analyse_url(payload: dict):
    if not _has_key():
        raise HTTPException(500, "GEMINI_API_KEY is not set. Add it to backend/.env and restart.")
    url = str(payload.get("url", "")).strip()
    try:
        data = peers.download_pdf(url)
    except peers.DownloadError as e:
        raise HTTPException(e.status, e.msg)
    company = next((c for c in load_catalog() if c["id"] == payload.get("company_id")), None)
    return _start_job(data, "Document received from company search", company, url)


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "Unknown job.")
    return JOBS[job_id]


@app.get("/api/jobs/{job_id}/report")
def download_report(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "Unknown job.")
    job_data = JOBS[job_id]
    if job_data.get("status") != "done":
        raise HTTPException(400, "Report is not ready yet.")
    result = job_data.get("result")
    if not result:
        raise HTTPException(500, "Analysis result is unavailable.")

    fd, report_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        generate_report(result, report_path)
    except Exception as e:
        os.remove(report_path)
        raise HTTPException(500, f"Could not generate PDF report: {str(e)[:300]}")

    company = result.get("meta", {}).get("company") or "ESG_Report"
    safe_company = "".join(c if c.isalnum() or c in " -_" else "_" for c in company).strip()
    return FileResponse(
        report_path,
        media_type="application/pdf",
        filename=f"{safe_company}_ESG_Intelligence_Report.pdf",
        background=BackgroundTask(os.remove, report_path),  # delete the temp file after sending
    )


@app.post("/api/controversies")
def controversies(payload: dict):
    """News scan for one company. Pass `job_id` of a finished analysis to add the say-do check."""
    company = str(payload.get("company", "")).strip()
    if not 2 <= len(company) <= 150:
        raise HTTPException(400, "Enter a company name.")
    try:
        days = max(7, min(180, int(payload.get("days", 90))))
    except (TypeError, ValueError):
        days = 90

    job_data = JOBS.get(str(payload.get("job_id", ""))) or {}
    findings = (job_data.get("result") or {}).get("f")

    llm = None
    if _has_key():
        from backend.llm import llm_json
        llm = lambda prompt: llm_json(prompt, temperature=0)  # noqa: E731 - same headline, same answer
    try:
        scan = controversy.scan(company, days, None, llm)
    except Exception as e:
        raise HTTPException(502, f"News scan failed: {str(e)[:300]}")

    # The say-do check depends on the caller's report, so it is added per request, not cached.
    return {**scan, "say_do": controversy.say_do_check(findings, scan["incidents"]) if findings else []}


BENCH: dict = {}
_bench_lock = threading.Lock()  # one peer analysis at a time keeps memory and LLM rate limits in check


def _bench_result(target: dict, rows: list) -> dict:
    ready = [r["record"] for r in rows if r.get("record")]
    return benchmark.compare(target, [{"id": r["id"], "name": r["name"], "findings": r["findings"],
                                        "meta": r.get("meta", {})} for r in ready])


def _bench_worker(b: dict) -> None:
    with _bench_lock:
        for row in b["peers"]:
            if row["state"] == "ready":
                continue
            company = next(c for c in load_catalog() if c["id"] == row["id"])
            row["state"] = "running"
            try:
                row["record"] = peers.analyse_company(company, on_msg=lambda m, r=row: r.update(msg=m))
                row.update(state="ready", msg="Analysed", source_url=row["record"]["source_url"])
            except Exception as e:  # noqa: BLE001 - one peer failing must not stop the others
                row.update(state="failed", msg=str(e)[:200])
            b["result"] = _bench_result(b["target"], b["peers"])
    b["status"] = "done"


@app.post("/api/benchmark")
def start_benchmark(payload: dict):
    """Benchmarks a finished analysis against up to 4 industry peers."""
    job_data = JOBS.get(str(payload.get("job_id", "")))
    if not job_data or job_data.get("status") != "done":
        raise HTTPException(400, "Run a report analysis first.")
    cid = payload.get("company_id") or job_data.get("company_id")
    company = next((c for c in load_catalog() if c["id"] == cid), None)
    if not company:
        raise HTTPException(400, "Choose which company this report belongs to.")
    if company["id"] != job_data.get("company_id"):  # the user corrected or set the company
        job_data["company_id"] = company["id"]
        peers.put(company, job_data["result"])

    rows = []
    for p in benchmark.peers_of(company, load_catalog(), int(payload.get("peers", 4) or 4)):
        rec = peers.get(p["id"])
        rows.append({"id": p["id"], "name": p["name"], "state": "ready" if rec else "queued",
                     "msg": "Using a stored analysis" if rec else "Waiting", "record": rec,
                     "source_url": rec.get("source_url", "") if rec else ""})
    if any(r["state"] == "queued" for r in rows) and not _has_key():
        raise HTTPException(500, "GEMINI_API_KEY is not set, so peer reports cannot be analysed.")

    target = {"id": company["id"], "name": company["name"], "findings": job_data["result"]["f"]}
    bid = uuid.uuid4().hex
    BENCH[bid] = {"status": "running", "industry": benchmark.industry_of(company), "target": target,
                  "peers": rows, "result": _bench_result(target, rows)}
    if all(r["state"] == "ready" for r in rows):
        BENCH[bid]["status"] = "done"
    else:
        threading.Thread(target=_bench_worker, args=(BENCH[bid],), daemon=True).start()
    return {"bench_id": bid}


@app.get("/api/benchmark/{bench_id}")
def get_benchmark(bench_id: str):
    b = BENCH.get(bench_id)
    if not b:
        raise HTTPException(404, "Unknown benchmark.")
    return {"status": b["status"], "industry": b["industry"], "target": b["target"]["name"],
            "peers": [{k: r.get(k) for k in ("id", "name", "state", "msg", "source_url")} for r in b["peers"]],
            "result": b["result"]}


@app.get("/")
def index():
    return FileResponse(ROOT / "index.html")


@app.get("/companies.json")
def companies():
    return FileResponse(ROOT / "companies.json")


@app.get("/favicon.svg")
def favicon():
    return FileResponse(ROOT / "favicon.svg")
