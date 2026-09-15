"""Integration tests for the translation-accuracy evaluation endpoint against a real
FastAPI app + DB, with a fake completed job/output and a real sidecar reference file on
disk (no network, no GPU -- the scoring itself is pure text, already unit-tested)."""
import json

import pytest
from fastapi.testclient import TestClient

from app.db.models import AudioStream, Job, MediaFile, Output, Transcript
from app.db.session import session_scope


@pytest.fixture
def library_root(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    return root


@pytest.fixture
def client(monkeypatch, library_root, tmp_path):
    monkeypatch.setenv("SUBTITLE_WORKER_THREADS", "0")
    monkeypatch.setenv("SUBTITLE_LIBRARY_DIR", str(library_root))
    monkeypatch.setenv("SUBTITLE_OUTPUT_DIR", str(tmp_path / "output"))
    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


def _seed_done_job_with_output(tmp_path) -> str:
    hypothesis_path = tmp_path / "hypothesis.srt"
    hypothesis_path.write_text(
        "1\n00:00:00,000 --> 00:00:05,000\nHello there, how are you\n\n"
        "2\n00:00:10,000 --> 00:00:15,000\nCompletely unrelated line\n"
    )
    with session_scope() as session:
        media = MediaFile(original_path="/x/video.mkv", filename="video.mkv", container_format="matroska",
                           size_bytes=1, file_hash="h", raw_ffprobe_json={})
        session.add(media)
        session.flush()
        job = Job(media_file_id=media.id, audio_stream_id=None, target_languages=["english"], status="done")
        session.add(job)
        session.flush()
        session.add(Output(job_id=job.id, format="srt", target_language="english",
                            file_path=str(hypothesis_path), provenance_json={}))
        session.commit()
        return job.id


@pytest.mark.integration
def test_evaluate_job_against_sidecar_reference_persists_and_returns_a_report(client, library_root, tmp_path):
    job_id = _seed_done_job_with_output(tmp_path)
    (library_root / "reference.srt").write_text(
        "1\n00:00:00,000 --> 00:00:05,000\nHello there, how are you\n\n"
        "2\n00:00:10,000 --> 00:00:15,000\nSomething else entirely different\n"
    )

    resp = client.post(f"/api/jobs/{job_id}/evaluate", json={"reference_srt_path": "reference.srt"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == job_id
    assert body["cue_count"] == 2
    assert body["matched_count"] == 2
    assert body["coverage"] == 1.0
    assert body["mean_chrf"] > 0.5  # one near-perfect match, one poor match -- averages above the poor one alone
    assert len(body["per_cue"]) == 2

    # persisted, and retrievable via the list endpoint
    listed = client.get(f"/api/jobs/{job_id}/evaluations").json()
    assert len(listed) == 1
    assert listed[0]["id"] == body["id"]


@pytest.mark.integration
def test_evaluate_job_requires_the_job_to_be_done(client, tmp_path):
    with session_scope() as session:
        media = MediaFile(original_path="/x/video.mkv", filename="video.mkv", container_format="matroska",
                           size_bytes=1, file_hash="h", raw_ffprobe_json={})
        session.add(media)
        session.flush()
        job = Job(media_file_id=media.id, audio_stream_id=None, target_languages=["english"], status="running")
        session.add(job)
        session.commit()
        job_id = job.id

    resp = client.post(f"/api/jobs/{job_id}/evaluate", json={"reference_srt_path": "reference.srt"})
    assert resp.status_code == 409


@pytest.mark.integration
def test_evaluate_job_rejects_both_or_neither_reference_source(client, tmp_path):
    job_id = _seed_done_job_with_output(tmp_path)

    assert client.post(f"/api/jobs/{job_id}/evaluate", json={}).status_code == 422
    resp = client.post(f"/api/jobs/{job_id}/evaluate", json={
        "reference_srt_path": "a.srt", "embedded_stream_index": 5,
    })
    assert resp.status_code == 422


@pytest.mark.integration
def test_evaluate_job_404s_for_missing_reference_file(client, tmp_path):
    job_id = _seed_done_job_with_output(tmp_path)
    resp = client.post(f"/api/jobs/{job_id}/evaluate", json={"reference_srt_path": "does-not-exist.srt"})
    assert resp.status_code == 422


def _seed_done_job_with_transcript(tmp_path, language="zh") -> str:
    transcript_path = tmp_path / "canonical_transcript.json"
    transcript_path.write_text(json.dumps({"segments": [
        {"start": 0.0, "end": 5.0, "text": "你好吗", "suppressed": False},
        {"start": 10.0, "end": 15.0, "text": "字幕组", "suppressed": True},  # excluded, like real hallucination-suppressed content
    ], "provenance": {}}))
    with session_scope() as session:
        media = MediaFile(original_path="/x/video.mkv", filename="video.mkv", container_format="matroska",
                           size_bytes=1, file_hash="h", raw_ffprobe_json={})
        session.add(media)
        session.flush()
        stream = AudioStream(media_file_id=media.id, stream_index=0, codec="aac", channels=2,
                              sample_rate=16000, duration_s=20.0, stream_hash="h2")
        session.add(stream)
        session.flush()
        job = Job(media_file_id=media.id, audio_stream_id=stream.id, target_languages=["english"], status="done")
        session.add(job)
        session.flush()
        session.add(Transcript(job_id=job.id, audio_stream_id=stream.id, engine="fake", model_version="fake:1",
                                language=language, raw_json_path=str(transcript_path)))
        session.commit()
        return job.id


@pytest.mark.integration
def test_evaluate_job_transcription_mode_scores_asr_against_source_language_reference(client, library_root, tmp_path):
    job_id = _seed_done_job_with_transcript(tmp_path)
    (library_root / "reference.srt").write_text("1\n00:00:00,000 --> 00:00:05,000\n你好吗\n")

    resp = client.post(f"/api/jobs/{job_id}/evaluate", json={
        "mode": "transcription", "reference_srt_path": "reference.srt",
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "transcription"
    assert body["target_language"] == "zh"
    assert body["cue_count"] == 1  # the suppressed segment is excluded, matching the rest of the pipeline
    assert body["mean_chrf"] == pytest.approx(1.0)


@pytest.mark.integration
def test_evaluate_job_transcription_mode_404s_without_a_transcript(client, library_root, tmp_path):
    job_id = _seed_done_job_with_output(tmp_path)  # has an Output but no Transcript row
    (library_root / "reference.srt").write_text("1\n00:00:00,000 --> 00:00:05,000\nHi\n")

    resp = client.post(f"/api/jobs/{job_id}/evaluate", json={
        "mode": "transcription", "reference_srt_path": "reference.srt",
    })
    assert resp.status_code == 404


@pytest.mark.integration
def test_evaluate_job_rejects_an_unknown_mode(client, tmp_path):
    job_id = _seed_done_job_with_output(tmp_path)
    resp = client.post(f"/api/jobs/{job_id}/evaluate", json={"mode": "bogus", "reference_srt_path": "x.srt"})
    assert resp.status_code == 422
