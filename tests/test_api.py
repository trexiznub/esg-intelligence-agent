"""API flow tests with FastAPI's TestClient (runs in CI, where FastAPI is installed)."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from backend import main, peers  # noqa: E402
from backend.company_search import resolve  # noqa: E402

client = TestClient(main.app)


def findings(disclosed):
    from backend.taxonomy import TOPICS
    return {c: [{"topic": t, "status": "Found" if t in disclosed else "Not found",
                 "confidence": "High" if t in disclosed else "", "value": "x", "page": 3} for t, _ in items]
            for c, items in TOPICS.items()}


def test_health_and_static():
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/companies.json").json()[0]["id"]


def test_unknown_company_search_is_400():
    assert client.post("/api/company-search", json={"company": "Random Startup"}).status_code == 400


def test_benchmark_uses_stored_peers(tmp_path, monkeypatch):
    monkeypatch.setattr(peers, "RUNTIME", tmp_path / "store.json")
    monkeypatch.setattr(peers, "SEED", tmp_path / "seed.json")
    for pid in ("tcs", "wipro", "hcltech", "techm"):
        peers.put(resolve(pid), {"f": findings({"Scope 3 emissions", "Water"}), "meta": {}}, path=peers.RUNTIME)
    main.JOBS["job1"] = {"status": "done", "company_id": None,
                         "result": {"f": findings({"Water"}), "meta": {"company": "Infosys Limited"}}}

    assert client.post("/api/benchmark", json={"job_id": "job1"}).status_code == 400  # company unknown
    r = client.post("/api/benchmark", json={"job_id": "job1", "company_id": "infosys"})
    assert r.status_code == 200
    b = client.get(f"/api/benchmark/{r.json()['bench_id']}").json()
    assert b["status"] == "done" and b["industry"] == "IT services"
    rows = {t["topic"]: t for t in b["result"]["topics"]}
    assert rows["Scope 3 emissions"]["position"] == "Lagging"
    assert len(rows["Scope 3 emissions"]["leaders"]) == 4


def test_benchmark_requires_finished_job():
    assert client.post("/api/benchmark", json={"job_id": "nope"}).status_code == 400
