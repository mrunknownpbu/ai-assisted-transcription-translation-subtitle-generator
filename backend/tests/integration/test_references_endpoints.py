"""Integration tests for the reference-archive browse endpoint and the evaluate endpoint's
archive-reference option, against a real synthetic reference folder rather than uploading
anything through a job."""
import pytest
from fastapi.testclient import TestClient

from app.db.models import AudioStream, Job, MediaFile, Output
from app.db.session import session_scope


@pytest.fixture
def reference_root(tmp_path):
    root = tmp_path / "references"
    (root / "Show").mkdir(parents=True)
    (root / "Show" / "episode1.srt").write_text(
        "1\n00:00:00,000 --> 00:00:05,000\nHello there, how are you\n"
    )
    (root / "Show" / "notes.txt").write_text("not a subtitle")
    return root


@pytest.fixture
def client(monkeypatch, reference_root, tmp_path):
    monkeypatch.setenv("SUBTITLE_WORKER_THREADS", "0")
    monkeypatch.setenv("SUBTITLE_REFERENCE_DIR", str(reference_root))
    monkeypatch.setenv("SUBTITLE_OUTPUT_DIR", str(tmp_path / "output"))
    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.mark.integration
def test_browse_references_root_lists_only_the_show_directory(client):
    resp = client.get("/api/references/browse")
    assert resp.status_code == 200
    assert resp.json() == [{"name": "Show", "path": "Show", "is_dir": True}]


@pytest.mark.integration
def test_browse_references_descends_and_filters_non_subtitle_files(client):
    resp = client.get("/api/references/browse", params={"path": "Show"})
    assert resp.status_code == 200
    names = {e["name"] for e in resp.json()}
    assert names == {"episode1.srt"}


@pytest.mark.integration
def test_browse_references_rejects_traversal(client):
    resp = client.get("/api/references/browse", params={"path": "../../etc"})
    assert resp.status_code == 422


@pytest.mark.integration
def test_references_404_when_not_configured(monkeypatch):
    monkeypatch.setenv("SUBTITLE_WORKER_THREADS", "0")
    monkeypatch.delenv("SUBTITLE_REFERENCE_DIR", raising=False)
    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        assert c.get("/api/references/browse").status_code == 404


def _seed_done_job_with_output(tmp_path) -> str:
    hypothesis_path = tmp_path / "hypothesis.srt"
    hypothesis_path.write_text("1\n00:00:00,000 --> 00:00:05,000\nHello there, how are you\n")
    with session_scope() as session:
        media = MediaFile(original_path="/x/video.mkv", filename="video.mkv", container_format="matroska",
                           size_bytes=1, file_hash="h", raw_ffprobe_json={})
        session.add(media)
        session.flush()
        stream = AudioStream(media_file_id=media.id, stream_index=0, codec="aac", channels=2,
                              sample_rate=16000, duration_s=5.0, stream_hash="h2")
        session.add(stream)
        session.flush()
        job = Job(media_file_id=media.id, audio_stream_id=stream.id, target_languages=["english"], status="done")
        session.add(job)
        session.flush()
        session.add(Output(job_id=job.id, format="srt", target_language="english",
                            file_path=str(hypothesis_path), provenance_json={}))
        session.commit()
        return job.id


@pytest.mark.integration
def test_evaluate_job_against_reference_archive_persists_and_returns_a_report(client, tmp_path):
    job_id = _seed_done_job_with_output(tmp_path)

    resp = client.post(f"/api/jobs/{job_id}/evaluate", json={"reference_archive_path": "Show/episode1.srt"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["mean_chrf"] == pytest.approx(1.0)
    assert body["reference_source"].startswith("archive:")


@pytest.mark.integration
def test_evaluate_job_rejects_more_than_one_reference_source(client, tmp_path):
    job_id = _seed_done_job_with_output(tmp_path)
    resp = client.post(f"/api/jobs/{job_id}/evaluate", json={
        "reference_srt_path": "a.srt", "reference_archive_path": "Show/episode1.srt",
    })
    assert resp.status_code == 422


@pytest.mark.integration
def test_evaluate_job_rejects_zero_reference_sources(client, tmp_path):
    job_id = _seed_done_job_with_output(tmp_path)
    assert client.post(f"/api/jobs/{job_id}/evaluate", json={}).status_code == 422
