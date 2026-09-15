import pytest

from app.db.models import AudioStream, Job, MediaFile
from app.db.session import init_db, session_scope


@pytest.mark.unit
def test_schema_creates_and_round_trips():
    init_db()
    with session_scope() as session:
        media = MediaFile(
            original_path="/data/media/sample.mkv", filename="sample.mkv",
            container_format="matroska", size_bytes=1234, file_hash="deadbeef",
            raw_ffprobe_json={"streams": []},
        )
        session.add(media)
        session.flush()

        stream = AudioStream(
            media_file_id=media.id, stream_index=0, codec="aac", channels=2,
            sample_rate=48000, duration_s=120.5, stream_hash="streamhash1",
        )
        session.add(stream)
        session.flush()

        job = Job(media_file_id=media.id, audio_stream_id=stream.id, target_languages=["es", "fr"])
        session.add(job)
        session.flush()
        job_id = job.id

    with session_scope() as session:
        fetched = session.get(Job, job_id)
        assert fetched is not None
        assert fetched.target_languages == ["es", "fr"]
        assert fetched.status == "queued"


@pytest.mark.unit
def test_atomic_claim_prevents_double_claim():
    """Simulates the queue-claim pattern two workers would race on: a single UPDATE ...
    WHERE status='queued' can only ever affect one row, so only one 'worker' wins."""
    from sqlalchemy import update

    init_db()
    with session_scope() as session:
        media = MediaFile(original_path="/x", filename="x.mkv", container_format="matroska",
                           size_bytes=1, file_hash="h", raw_ffprobe_json={})
        session.add(media)
        session.flush()
        stream = AudioStream(media_file_id=media.id, stream_index=0, codec="aac", channels=2,
                              sample_rate=16000, duration_s=1.0, stream_hash="h2")
        session.add(stream)
        session.flush()
        job = Job(media_file_id=media.id, audio_stream_id=stream.id)
        session.add(job)
        session.flush()
        job_id = job.id

    results = []
    for worker_id in ("worker-a", "worker-b"):
        with session_scope() as session:
            stmt = (
                update(Job)
                .where(Job.id == job_id, Job.status == "queued")
                .values(status="running", locked_by=worker_id)
            )
            res = session.execute(stmt)
            results.append(res.rowcount)

    assert sorted(results) == [0, 1]
