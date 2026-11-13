"""Peer data: report download, the analysed-company store and peer analysis runs.

Shared by the API (backend/main.py) and the offline seeding script
(scripts/build_benchmarks.py).

Store layout: {company_id: {"id", "name", "findings", "meta", "source_url", "analysed_at"}}
  data/benchmarks.json  committed seed, read-only at runtime
  data/peer_store.json  written at runtime (lost when a free host restarts)
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from backend import pipeline
from backend.company_search import _key, load_catalog, search_company_reports

DATA = Path(__file__).resolve().parent.parent / "data"
SEED = DATA / "benchmarks.json"
RUNTIME = DATA / "peer_store.json"
MAX_MB = 100
MAX_AGE_DAYS = 550  # reports are annual; reuse an analysis for about 18 months


class DownloadError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status, self.msg = status, msg


# ---------- download ----------
def assert_public_url(url: str) -> None:
    """Blocks requests to private, loopback and link-local addresses (SSRF)."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise DownloadError(400, "A valid report URL is required.")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror:
        raise DownloadError(400, "The report host could not be resolved.")
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise DownloadError(400, "That address is not a public website.")


def download_pdf(url: str) -> bytes:
    import requests
    from backend.site_crawler import BROWSER_HEADERS

    assert_public_url(url)
    headers = {**BROWSER_HEADERS, "Accept": "application/pdf,*/*;q=0.8"}
    try:
        target = url
        for _ in range(4):  # follow up to 3 redirects, re-checking each target
            response = requests.get(target, timeout=(10, 60), stream=True, allow_redirects=False, headers=headers)
            if not response.is_redirect:
                break
            target = urljoin(target, response.headers.get("location", ""))
            assert_public_url(target)
        else:
            raise DownloadError(502, "The report URL redirected too many times.")
        if response.status_code in (401, 403):
            raise DownloadError(502, "The company website blocked the automatic download. Open the report, "
                                     "save the PDF and upload it instead.")
        response.raise_for_status()
        data = b""
        for chunk in response.iter_content(1 << 16):
            data += chunk
            if len(data) > MAX_MB * 1048576:
                raise DownloadError(413, f"Report exceeds {MAX_MB} MB.")
    except DownloadError:
        raise
    except Exception as e:  # noqa: BLE001
        raise DownloadError(502, f"Could not download report: {str(e)[:300]}")
    if not data.startswith(b"%PDF"):
        raise DownloadError(400, "The selected URL did not return a PDF file.")
    return data


# ---------- company detection ----------
def detect_company(text: str):
    """Catalogue company whose name or alias appears in `text` (longest match wins)."""
    k = f" {_key(text or '')} "
    best = None
    for c in load_catalog():
        for n in (c["name"], *c["aliases"]):
            nk = _key(n)
            if len(nk) >= 3 and f" {nk} " in k and (best is None or len(nk) > best[0]):
                best = (len(nk), c)
    return best[1] if best else None


# ---------- store ----------
_lock = threading.Lock()


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def all_records() -> dict:
    return {**_read(SEED), **_read(RUNTIME)}


def get(company_id: str):
    rec = all_records().get(company_id)
    if rec and time.time() - rec.get("analysed_at", 0) < MAX_AGE_DAYS * 86400:
        return rec
    return None


def put(company: dict, result: dict, source_url: str = "", path: Path | None = None) -> dict:
    path = path or RUNTIME
    rec = {"id": company["id"], "name": company["name"], "findings": result["f"], "meta": result.get("meta", {}),
           "source_url": source_url, "analysed_at": int(time.time())}
    with _lock:
        data = _read(path)
        data[company["id"]] = rec
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass
    return rec


# ---------- peer analysis ----------
_PREF = {"India": ["BRSR", "ESG Report", "Sustainability Report", "Integrated Report", "Impact Report",
                   "Climate Report", "Annual Report"],
         "Global": ["Sustainability Report", "ESG Report", "Impact Report", "Integrated Report", "Climate Report",
                    "Annual Report"]}


def pick_report(reports: list, region: str):
    """Best PDF for an ESG comparison: preferred report type, then the latest year."""
    pref = _PREF.get(region, _PREF["Global"])
    pdfs = [r for r in reports if r.get("pdf")]
    if not pdfs:
        return None
    year = lambda r: r["year"] if re.match(r"20\d\d", str(r.get("year", ""))) else "0"
    rank = lambda r: pref.index(r["type"]) if r.get("type") in pref else len(pref)
    return sorted(pdfs, key=lambda r: (-int(year(r)[:4]), rank(r)))[0] if any(year(r) != "0" for r in pdfs) \
        else sorted(pdfs, key=rank)[0]


def analyse_company(company: dict, on_msg=lambda m: None, search=search_company_reports) -> dict:
    """Finds, downloads and analyses a company's latest ESG report, then stores the result."""
    on_msg("Finding the latest report")
    found = search(company["id"])
    report = pick_report(found.get("reports", []), company["region"])
    if not report:
        raise RuntimeError("no report PDF found on official sources")
    on_msg(f"Downloading {report['title'][:80]}")
    data = download_pdf(report["url"])
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.write(fd, data)
    os.close(fd)
    job = {"status": "running", "stage": 0, "msg": "", "progress": 0, "result": None, "error": None}
    on_msg("Analysing the report")
    pipeline.run(job, path)  # deletes the temp file when finished
    if job["status"] != "done":
        raise RuntimeError(job.get("error") or "analysis failed")
    return put(company, job["result"], report["url"])
