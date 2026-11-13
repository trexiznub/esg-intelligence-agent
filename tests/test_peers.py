"""Peer store, company detection and report choice. No network."""
import pytest

from backend import peers
from backend.company_search import resolve


@pytest.mark.parametrize("text,expected", [
    ("Infosys Limited", "infosys"), ("TATA CONSULTANCY SERVICES LIMITED", "tcs"),
    ("Hindustan Unilever Limited (HUL)", "hul"), ("Unilever PLC", "unilever"),
    ("Tata Steel Limited", "tatasteel"), ("Meridian Components Ltd (fictional)", None), ("", None)])
def test_detect_company(text, expected):
    c = peers.detect_company(text)
    assert (c["id"] if c else None) == expected


def test_pick_report_prefers_latest_then_esg_types():
    reps = [{"pdf": True, "type": "Annual Report", "year": "2024-25", "url": "a"},
            {"pdf": True, "type": "BRSR", "year": "2024-25", "url": "b"},
            {"pdf": True, "type": "ESG Report", "year": "2022", "url": "c"},
            {"pdf": False, "type": "BRSR", "year": "2025", "url": "d"}]
    assert peers.pick_report(reps, "India")["url"] == "b"
    assert peers.pick_report(reps[:1] + reps[2:3], "Global")["url"] == "a"
    assert peers.pick_report([{"pdf": True, "type": "Annual Report", "year": "See document", "url": "x"},
                              {"pdf": True, "type": "ESG Report", "year": "See document", "url": "y"}], "Global")["url"] == "y"
    assert peers.pick_report(reps[3:], "India") is None


def test_store_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(peers, "RUNTIME", tmp_path / "store.json")
    monkeypatch.setattr(peers, "SEED", tmp_path / "seed.json")
    assert peers.get("tcs") is None
    peers.put(resolve("TCS"), {"f": {"Environmental": []}, "meta": {"year": "FY25"}}, "https://tcs.com/r.pdf",
              path=peers.RUNTIME)
    rec = peers.get("tcs")
    assert rec["name"] == "Tata Consultancy Services" and rec["source_url"].endswith("r.pdf")


def test_analyse_company_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(peers, "RUNTIME", tmp_path / "store.json")
    monkeypatch.setattr(peers, "download_pdf", lambda url: b"%PDF-1.4 fake")

    def fake_run(job, path):
        job.update(status="done", result={"f": {"Social": []}, "meta": {"company": "Wipro"}})

    monkeypatch.setattr(peers.pipeline, "run", fake_run)
    search = lambda cid: {"reports": [{"pdf": True, "type": "ESG Report", "year": "2025", "url": "https://wipro.com/esg.pdf",
                                       "title": "Wipro ESG Report 2025"}]}
    msgs = []
    rec = peers.analyse_company(resolve("Wipro"), on_msg=msgs.append, search=search)
    assert rec["id"] == "wipro" and peers.get("wipro")["source_url"] == "https://wipro.com/esg.pdf"
    assert msgs[0] == "Finding the latest report"
    with pytest.raises(RuntimeError):
        peers.analyse_company(resolve("TCS"), search=lambda cid: {"reports": []})


def test_ssrf_guard_blocks_private_addresses():
    for url in ("http://127.0.0.1/a.pdf", "http://169.254.169.254/", "ftp://x.com/a.pdf"):
        with pytest.raises(peers.DownloadError):
            peers.download_pdf(url)
