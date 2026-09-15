import pytest
from sqlalchemy import select, update

from app.db.models import AudioStream, GpuSlot, Job, MediaFile
from app.db.session import init_db, session_scope
from app.jobs.queue import claim_next_job, enqueue_job, init_gpu_slots, recover_orphaned_jobs, try_acquire_gpu_slot


def _seed_media_and_stream(session):
    media = MediaFile(original_path="/x", filename="x.mkv", container_format="matroska",
                       size_bytes=1, file_hash="h", raw_ffprobe_json={})
    session.add(media)
    session.flush()
    stream = AudioStream(media_file_id=media.id, stream_index=0, codec="aac", channels=2,
                          sample_rate=16000, duration_s=1.0, stream_hash="h2")
    session.add(stream)
    session.flush()
    return media.id, stream.id


@pytest.mark.unit
def test_recover_orphaned_jobs_requeues_a_running_job_with_no_owning_worker():
    init_db()
    with session_scope() as session:
        media_id, stream_id = _seed_media_and_stream(session)
        job = enqueue_job(session, media_file_id=media_id, audio_stream_id=stream_id, target_languages=["es"])
        job_id = job.id
        # Simulate what claim_next_job would have done in the process that died mid-run.
        session.execute(update(Job).where(Job.id == job_id).values(status="running", locked_by="dead-worker"))
        session.commit()

    with session_scope() as session:
        recovered = recover_orphaned_jobs(session)
        assert recovered == [job_id]

    with session_scope() as session:
        refreshed = session.get(Job, job_id)
        # max_retries defaults to 2 and retry_count starts at 0, so this goes back to queued
        # rather than straight to failed -- same as any other crash mid-run.
        assert refreshed.status == "queued"
        assert refreshed.retry_count == 1
        assert "restart" in refreshed.error_message


@pytest.mark.unit
def test_recover_orphaned_jobs_fails_a_job_that_has_exhausted_its_retries():
    init_db()
    with session_scope() as session:
        media_id, stream_id = _seed_media_and_stream(session)
        job = enqueue_job(session, media_file_id=media_id, audio_stream_id=stream_id, target_languages=["es"],
                           max_retries=0)
        job_id = job.id
        session.execute(update(Job).where(Job.id == job_id).values(status="running"))
        session.commit()

    with session_scope() as session:
        recover_orphaned_jobs(session)

    with session_scope() as session:
        assert session.get(Job, job_id).status == "failed"


@pytest.mark.unit
def test_recover_orphaned_jobs_releases_the_gpu_slot_it_was_holding():
    init_db()
    with session_scope() as session:
        media_id, stream_id = _seed_media_and_stream(session)
        job = enqueue_job(session, media_file_id=media_id, audio_stream_id=stream_id, target_languages=["es"])
        job_id = job.id
        init_gpu_slots(session, 1)
        assert try_acquire_gpu_slot(session, job_id) is True
        session.execute(update(Job).where(Job.id == job_id).values(status="running"))
        session.commit()

    with session_scope() as session:
        recover_orphaned_jobs(session)

    with session_scope() as session:
        slot = session.execute(select(GpuSlot)).scalars().one()
        assert slot.held_by is None

    # A brand-new job can now actually get the slot instead of queuing forever behind
    # the dead job's permanently-wedged hold.
    with session_scope() as session:
        media_id, stream_id = _seed_media_and_stream(session)
        new_job = enqueue_job(session, media_file_id=media_id, audio_stream_id=stream_id, target_languages=["es"])
        assert try_acquire_gpu_slot(session, new_job.id) is True


@pytest.mark.unit
def test_recover_orphaned_jobs_leaves_queued_and_done_jobs_untouched():
    init_db()
    with session_scope() as session:
        media_id, stream_id = _seed_media_and_stream(session)
        queued_job = enqueue_job(session, media_file_id=media_id, audio_stream_id=stream_id, target_languages=["es"])
        done_job = enqueue_job(session, media_file_id=media_id, audio_stream_id=stream_id, target_languages=["es"])
        session.execute(update(Job).where(Job.id == done_job.id).values(status="done"))
        session.commit()
        queued_id, done_id = queued_job.id, done_job.id

    with session_scope() as session:
        assert recover_orphaned_jobs(session) == []

    with session_scope() as session:
        assert session.get(Job, queued_id).status == "queued"
        assert session.get(Job, done_id).status == "done"


@pytest.mark.unit
def test_recover_orphaned_jobs_handles_no_running_jobs_at_all():
    init_db()
    with session_scope() as session:
        assert recover_orphaned_jobs(session) == []


@pytest.mark.unit
def test_reclaiming_a_recovered_job_clears_the_stale_orphaned_error_message():
    """Regression test: once a fresh attempt actually starts, the reason a *previous*
    attempt stopped is no longer current and must not linger on the job -- otherwise a
    healthy, actively-running job displays a stale "Orphaned..." message as if it were
    failing right now (caught via a real screenshot of the job detail page)."""
    init_db()
    with session_scope() as session:
        media_id, stream_id = _seed_media_and_stream(session)
        job = enqueue_job(session, media_file_id=media_id, audio_stream_id=stream_id, target_languages=["es"])
        job_id = job.id
        session.execute(update(Job).where(Job.id == job_id).values(status="running"))
        session.commit()

    with session_scope() as session:
        recover_orphaned_jobs(session)

    with session_scope() as session:
        assert session.get(Job, job_id).error_message is not None  # set by the recovery itself

    with session_scope() as session:
        claimed = claim_next_job(session, worker_id="worker-2")
        assert claimed.id == job_id
        assert claimed.error_message is None
