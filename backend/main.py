"""FastAPI app: report analysis jobs, company report search and Controversy Watch.

Run from the repository root:  uvicorn backend.main:app --port 8000
"""
import ipaddress
import os
import socket
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from backend import controversy, pipeline
from backend.company_search import search_company_reports
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
SCAN_TTL = 30 * 60     # seconds a news scan is cached
JOBS: dict = {}
SCANS: dict = {}


def _has_key() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def _start_job(data: bytes, msg: str) -> dict:
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
    JOBS[job_id] = {"status": "running", "stage": 0, "msg": msg, "progress": 0, "result": None, "error": None}
    threading.Thread(target=pipeline.run, args=(JOBS[job_id], path), daemon=True).start()
    return {"job_id": job_id}


def _assert_public_url(url: str) -> None:
    """Blocks server-side requests to private, loopback and link-local addresses (SSRF)."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(400, "A valid report URL is required.")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror:
        raise HTTPException(400, "The report host could not be resolved.")
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise HTTPException(400, "That address is not a public website.")


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
    except Exception as e:
        raise HTTPException(502, f"Company search failed: {str(e)[:300]}")


@app.post("/api/analyse")
async def analyse(file: UploadFile = File(...)):
    if not _has_key():
        raise HTTPException(500, "GEMINI_API_KEY is not set. Add it to backend/.env and restart.")
    return _start_job(await file.read(), "Document received")


@app.post("/api/analyse-url")
def analyse_url(payload: dict):
    import requests

    if not _has_key():
        raise HTTPException(500, "GEMINI_API_KEY is not set. Add it to backend/.env and restart.")

    url = str(payload.get("url", "")).strip()
    _assert_public_url(url)

    try:
        response = requests.get(
            url, timeout=30, stream=True, allow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 ESG-Intelligence-Agent"},
        )
        if response.is_redirect:  # follow one redirect, re-checking the target
            target = requests.compat.urljoin(url, response.headers.get("location", ""))
            _assert_public_url(target)
            response = requests.get(
                target, timeout=30, stream=True, allow_redirects=False,
                headers={"User-Agent": "Mozilla/5.0 ESG-Intelligence-Agent"},
            )
        response.raise_for_status()
        data = b""
        for chunk in response.iter_content(1 << 16):  # stop reading once over the limit
            data += chunk
            if len(data) > MAX_MB * 1048576:
                raise HTTPException(413, f"Report exceeds {MAX_MB} MB.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Could not download report: {str(e)[:300]}")

    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "The selected URL did not return a PDF file.")
    return _start_job(data, "Document received from company search")


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

    key = (company.lower(), days)
    cached = SCANS.get(key)
    if cached and time.time() - cached["at"] < SCAN_TTL:
        scan = cached["scan"]
    else:
        llm_json = None
        if _has_key():
            from backend.llm import llm_json
        try:
            scan = controversy.scan(company, days, None, llm_json)
        except Exception as e:
            raise HTTPException(502, f"News scan failed: {str(e)[:300]}")
        for old in [k for k, v in SCANS.items() if time.time() - v["at"] >= SCAN_TTL]:
            del SCANS[old]
        SCANS[key] = {"at": time.time(), "scan": scan}

    # The say-do check depends on the caller's report, so it is added per request, not cached.
    return {**scan, "say_do": controversy.say_do_check(findings, scan["incidents"]) if findings else []}


@app.get("/")
def index():
    return FileResponse(ROOT / "index.html")


@app.get("/favicon.svg")
def favicon():
    return FileResponse(ROOT / "favicon.svg")
