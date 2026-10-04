import os
import tempfile
import threading
import uuid
from pathlib import Path
from fastapi.responses import FileResponse
from backend.report import generate_report



from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from backend import pipeline

load_dotenv(Path(__file__).parent / ".env")

app = FastAPI(title="ESG Intelligence Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5501",
        "http://localhost:5501",
        "https://trexiznub.github.io",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_MB = 100
JOBS = {}


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "api_key_configured": bool(os.getenv("GEMINI_API_KEY")),
    }


@app.post("/api/analyse")
async def analyse(file: UploadFile = File(...)):
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(
            500,
            "GEMINI_API_KEY is not set. Add it to backend/.env and restart.",
        )

    data = await file.read()

    if len(data) > MAX_MB * 1048576:
        raise HTTPException(
            413,
            f"File exceeds {MAX_MB} MB.",
        )

    if not data.startswith(b"%PDF"):
        raise HTTPException(
            400,
            "Only valid PDF files are supported.",
        )

    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.write(fd, data)
    os.close(fd)

    job_id = uuid.uuid4().hex

    JOBS[job_id] = {
        "status": "running",
        "stage": 0,
        "msg": "Document received",
        "progress": 0,
        "result": None,
        "error": None,
    }

    threading.Thread(
        target=pipeline.run,
        args=(JOBS[job_id], path),
        daemon=True,
    ).start()

    return {"job_id": job_id}


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
        raise HTTPException(
            400,
            "Report is not ready yet."
        )

    result = job_data.get("result")

    if not result:
        raise HTTPException(
            500,
            "Analysis result is unavailable."
        )

    fd, report_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)

    try:
        generate_report(result, report_path)

        company = (
            result.get("meta", {}).get("company")
            or "ESG_Report"
        )

        safe_company = "".join(
            c if c.isalnum() or c in " -_" else "_"
            for c in company
        ).strip()

        filename = f"{safe_company}_ESG_Intelligence_Report.pdf"

        return FileResponse(
            report_path,
            media_type="application/pdf",
            filename=filename,
        )

    except Exception as e:
        try:
            os.remove(report_path)
        except OSError:
            pass

        raise HTTPException(
            500,
            f"Could not generate PDF report: {str(e)}"
        )