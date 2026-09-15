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
def test_settings_endpoint_exposes_no_secrets(client, monkeypatch):
    monkeypatch.setenv("TVDB_API_KEY", "super-secret-key")
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tvdb_configured"] is True
    assert "super-secret-key" not in resp.text
    assert set(body.keys()) == {
        "whisper_model_size", "nllb_model_name", "max_concurrent_gpu_jobs",
        "library_configured", "tvdb_configured",
    }


@pytest.mark.integration
def test_settings_reports_library_not_configured_by_default(client, monkeypatch):
    monkeypatch.delenv("SUBTITLE_LIBRARY_DIR", raising=False)
    resp = client.get("/api/settings")
    assert resp.json()["library_configured"] is False
