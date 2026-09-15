import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SUBTITLE_WORKER_THREADS", "0")
    monkeypatch.setenv("SUBTITLE_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("SUBTITLE_OUTPUT_DIR", str(tmp_path / "output"))
    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.mark.integration
def test_storage_endpoint_returns_real_disk_usage_not_fabricated_numbers(client):
    resp = client.get("/api/hardware/storage")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_bytes"] > 0
    assert body["used_bytes"] >= 0
    assert body["free_bytes"] >= 0
    # used + free should roughly reconcile with total (filesystem overhead aside)
    assert body["used_bytes"] + body["free_bytes"] <= body["total_bytes"] * 1.01
