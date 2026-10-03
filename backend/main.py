import os, tempfile, threading, uuid
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from backend import pipeline

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5501",
        "http://localhost:5501",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_MB = 25
app = FastAPI(title="ESG Intelligence Agent")
JOBS = {}

@app.get("/api/health")
def health():
    return {"ok": True, "api_key_configured": bool(os.getenv("GEMINI_API_KEY"))}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5501", "http://localhost:5501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/analyse")
async def analyse(file: UploadFile = File(...)):
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(
            500,
            "GEMINI_API_KEY is not set. Add it to backend/.env and restart."
        )

    data = await file.read()

    if len(data) > MAX_MB * 1048576:
        raise HTTPException(
            413,
            f"File exceeds {MAX_MB} MB."
        )

    if not data.startswith(b"%PDF"):
        raise HTTPException(
            400,
            "Only valid PDF files are supported."
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
        "error": None
    }

    threading.Thread(
        target=pipeline.run,
        args=(JOBS[job_id], path),
        daemon=True
    ).start()

    return {"job_id": job_id}

@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    if job_id not in JOBS: raise HTTPException(404, "Unknown job.")
    return JOBS[job_id]


