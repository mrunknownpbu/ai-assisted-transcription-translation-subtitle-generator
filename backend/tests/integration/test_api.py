"""API-layer integration tests. Worker threads are disabled here (SUBTITLE_WORKER_THREADS=0)
so these tests exercise routing, validation, and queue-state transitions quickly, without
waiting on real ASR/translation — the full pipeline is already covered end to end in
test_end_to_end_pipeline.py.
"""
import pytest
from fastapi.testclient import TestClient

from tests.fixtures.synthetic_media import build_multi_stream_fixture


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SUBTITLE_WORKER_THREADS", "0")
    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.mark.integration
def test_health_check(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.integration
def test_hardware_endpoint_reports_a_vendor(client):
    resp = client.get("/api/hardware")
    assert resp.status_code == 200
    body = resp.json()
    assert body["vendor"] in ("nvidia", "rocm", "cpu")
    assert body["cpu_cores"] >= 1


@pytest.mark.integration
def test_upload_inspects_and_ranks_streams(client, ffmpeg_fixture_dir):
    media_path = build_multi_stream_fixture(ffmpeg_fixture_dir)
    with open(media_path, "rb") as f:
        resp = client.post("/api/media/upload", files={"file": ("multi_stream.mkv", f, "video/x-matroska")})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["audio_streams"]) == 3
    auto_selected = [s for s in body["audio_streams"] if s["selected_by"] == "auto"]
    assert len(auto_selected) == 1
    assert auto_selected[0]["stream_index"] == 1  # the speech stream, by fixture construction


@pytest.mark.integration
def test_manual_stream_override(client, ffmpeg_fixture_dir):
    media_path = build_multi_stream_fixture(ffmpeg_fixture_dir)
    with open(media_path, "rb") as f:
        upload = client.post("/api/media/upload", files={"file": ("m.mkv", f, "video/x-matroska")}).json()

    resp = client.post(f"/api/media/{upload['id']}/select-stream", json={"stream_index": 2})
    assert resp.status_code == 200
    selected = [s for s in resp.json()["audio_streams"] if s["selected_by"] == "manual"]
    assert len(selected) == 1
    assert selected[0]["stream_index"] == 2


@pytest.mark.integration
def test_job_lifecycle_queue_pause_resume_cancel(client, ffmpeg_fixture_dir):
    media_path = build_multi_stream_fixture(ffmpeg_fixture_dir)
    with open(media_path, "rb") as f:
        media = client.post("/api/media/upload", files={"file": ("m.mkv", f, "video/x-matroska")}).json()
    stream_id = next(s["id"] for s in media["audio_streams"] if s["selected_by"] == "auto")

    create_resp = client.post("/api/jobs", json={
        "media_file_id": media["id"], "audio_stream_id": stream_id,
        "target_languages": ["es", "fr"], "output_formats": ["srt"],
    })
    assert create_resp.status_code == 200
    job = create_resp.json()
    assert job["status"] == "queued"
    assert job["target_languages"] == ["es", "fr"]

    assert client.post(f"/api/jobs/{job['id']}/pause").status_code == 200
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "paused"

    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 200
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "queued"

    reorder = client.patch(f"/api/jobs/{job['id']}/priority", json={"priority": 5})
    assert reorder.status_code == 200
    assert reorder.json()["priority"] == 5

    cancel_resp = client.post(f"/api/jobs/{job['id']}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "canceled"

    # A canceled job can no longer be paused/resumed.
    assert client.post(f"/api/jobs/{job['id']}/pause").status_code == 409


@pytest.mark.integration
def test_second_job_for_same_media_requires_keep_replace_decision(client, ffmpeg_fixture_dir):
    media_path = build_multi_stream_fixture(ffmpeg_fixture_dir)
    with open(media_path, "rb") as f:
        media = client.post("/api/media/upload", files={"file": ("m.mkv", f, "video/x-matroska")}).json()
    stream_id = next(s["id"] for s in media["audio_streams"] if s["selected_by"] == "auto")

    job_payload = {"media_file_id": media["id"], "audio_stream_id": stream_id,
                   "target_languages": ["es"], "output_formats": ["srt"]}
    first_job = client.post("/api/jobs", json=job_payload).json()
    assert first_job["status"] == "queued"  # no prior output yet, starts normally

    # Manually create an Output row to simulate the first job having already produced
    # subtitles for this media file, then submit a second job for the same media.
    from app.db.models import Output
    from app.db.session import session_scope
    with session_scope() as session:
        session.add(Output(job_id=first_job["id"], format="srt", target_language="es",
                            file_path="/data/output/x.srt", provenance_json={}))
        session.commit()

    second_job = client.post("/api/jobs", json=job_payload).json()
    assert second_job["status"] == "needs_decision"  # held until KEEP/REPLACE is decided

    decide = client.post(f"/api/jobs/{second_job['id']}/existing-output-decision", json={"decision": "replace"})
    assert decide.status_code == 200
    assert decide.json()["status"] == "queued"


@pytest.mark.integration
def test_job_creation_rejects_unknown_media(client):
    resp = client.post("/api/jobs", json={
        "media_file_id": "does-not-exist", "audio_stream_id": "does-not-exist",
        "target_languages": ["es"],
    })
    assert resp.status_code == 404


@pytest.mark.integration
def test_job_creation_accepts_optional_glossary_entities(client, ffmpeg_fixture_dir):
    media_path = build_multi_stream_fixture(ffmpeg_fixture_dir)
    with open(media_path, "rb") as f:
        media = client.post("/api/media/upload", files={"file": ("m.mkv", f, "video/x-matroska")}).json()
    stream_id = next(s["id"] for s in media["audio_streams"] if s["selected_by"] == "auto")

    resp = client.post("/api/jobs", json={
        "media_file_id": media["id"], "audio_stream_id": stream_id, "target_languages": ["es"],
        "glossary_entities": [{"canonical": "Eda", "aliases": ["Edacim"]}], "tvdb_id": 12345,
    })

    assert resp.status_code == 200
    assert resp.json()["job_type"] == "audio_pipeline"


@pytest.mark.integration
def test_direct_translation_job_type(client, tmp_path):
    srt_path = tmp_path / "input.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello there\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nHow are you\n",
        encoding="utf-8",
    )

    with open(srt_path, "rb") as f:
        resp = client.post(
            "/api/jobs/direct-translation",
            files={"file": ("input.srt", f, "application/x-subrip")},
            data={"target_languages": "es", "output_formats": "srt"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["job_type"] == "direct_translation"
    assert body["media_file_id"] is None
    assert body["input_filename"] == "input.srt"
    assert body["status"] == "queued"


@pytest.mark.integration
def test_direct_translation_job_rejects_non_srt_file(client, tmp_path):
    txt_path = tmp_path / "input.txt"
    txt_path.write_text("not a subtitle file", encoding="utf-8")

    with open(txt_path, "rb") as f:
        resp = client.post(
            "/api/jobs/direct-translation",
            files={"file": ("input.txt", f, "text/plain")},
            data={"target_languages": "es"},
        )

    assert resp.status_code == 422
