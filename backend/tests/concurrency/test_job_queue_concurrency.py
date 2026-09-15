import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from app.db.models import AudioStream, Job, MediaFile
from app.db.session import init_db, session_scope
from app.jobs.queue import (
    claim_next_job, enqueue_job, init_gpu_slots, mark_job_done, release_gpu_slot, try_acquire_gpu_slot,
)


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


@pytest.mark.concurrency
def test_concurrent_workers_never_double_claim_or_drop_jobs():
    init_db()
    with session_scope() as session:
        media_id, stream_id = _seed_media_and_stream(session)
        job_ids = [enqueue_job(session, media_file_id=media_id, audio_stream_id=stream_id,
                                target_languages=["es"]).id for _ in range(30)]

    claimed: list[str] = []
    lock = threading.Lock()

    def worker_loop(worker_id: str):
        local_claims = []
        while True:
            with session_scope() as session:
                job = claim_next_job(session, worker_id=worker_id)
            if job is None:
                break
            local_claims.append(job.id)
        with lock:
            claimed.extend(local_claims)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker_loop, f"worker-{i}") for i in range(8)]
        for f in as_completed(futures):
            f.result()

    assert sorted(claimed) == sorted(job_ids)  # every job claimed exactly once, none dropped
    assert len(claimed) == len(set(claimed))  # no duplicates


@pytest.mark.concurrency
def test_gpu_semaphore_never_exceeds_total_slots_under_concurrent_acquire():
    init_db()
    total_slots = 3
    with session_scope() as session:
        init_gpu_slots(session, total_slots)

    successes: list[str] = []
    lock = threading.Lock()

    def try_job(job_id: str):
        with session_scope() as session:
            acquired = try_acquire_gpu_slot(session, job_id)
        if acquired:
            with lock:
                successes.append(job_id)

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(try_job, f"job-{i}") for i in range(10)]
        for f in as_completed(futures):
            f.result()

    assert len(successes) == total_slots  # exactly the slot count succeeded, never more

    with session_scope() as session:
        from app.db.models import GpuSlot
        held = [row.held_by for row in session.query(GpuSlot).all() if row.held_by is not None]
    assert len(held) == total_slots
    assert len(set(held)) == total_slots  # no slot double-assigned to two different jobs


@pytest.mark.concurrency
def test_gpu_slot_can_be_released_and_reacquired():
    init_db()
    with session_scope() as session:
        init_gpu_slots(session, 1)
        assert try_acquire_gpu_slot(session, "job-a") is True
        assert try_acquire_gpu_slot(session, "job-b") is False  # only slot already held

        release_gpu_slot(session, "job-a")
        assert try_acquire_gpu_slot(session, "job-b") is True


@pytest.mark.concurrency
def test_job_status_transitions_are_race_free_under_concurrent_completion_attempts():
    """Two threads racing to mark the same job done/failed must not corrupt state — the
    final state must be exactly one consistent outcome, not a torn write."""
    init_db()
    with session_scope() as session:
        media_id, stream_id = _seed_media_and_stream(session)
        job = enqueue_job(session, media_file_id=media_id, audio_stream_id=stream_id, target_languages=["es"])
        job_id = job.id
        claim_next_job(session, worker_id="w1")

    def complete():
        with session_scope() as session:
            mark_job_done(session, job_id)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(complete) for _ in range(5)]
        for f in as_completed(futures):
            f.result()

    with session_scope() as session:
        final_job = session.get(Job, job_id)
        assert final_job.status == "done"
        assert final_job.progress_pct == 100.0
