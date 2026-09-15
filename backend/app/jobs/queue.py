"""Job queue: atomic claim/cancel/retry/priority operations against the SQLite-backed
`jobs` table, plus the DB-backed GPU slot semaphore. Every mutation here is a single
UPDATE ... WHERE statement whose rowcount tells the caller whether it actually won the
race — never a read-then-write pair — so this is safe under concurrent worker
threads/processes without any additional locking.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.db.models import GpuSlot, Job


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def enqueue_job(
    session: Session, *, media_file_id: str, audio_stream_id: str, target_languages: list[str],
    output_formats: list[str] | None = None, source_language: str | None = None, priority: int = 0,
    max_retries: int = 2, glossary_entities: list[dict] | None = None, tvdb_id: int | None = None,
) -> Job:
    job = Job(
        job_type="audio_pipeline", media_file_id=media_file_id, audio_stream_id=audio_stream_id,
        target_languages=target_languages, output_formats=output_formats or ["srt", "vtt", "webvtt"],
        source_language=source_language, priority=priority, max_retries=max_retries,
        glossary_entities=glossary_entities, tvdb_id=tvdb_id,
    )
    session.add(job)
    session.flush()
    return job


def enqueue_direct_translation_job(
    session: Session, *, input_srt_path: str, input_filename: str, target_languages: list[str],
    output_formats: list[str] | None = None, source_language: str | None = None, priority: int = 0,
    max_retries: int = 2, glossary_entities: list[dict] | None = None, tvdb_id: int | None = None,
) -> Job:
    job = Job(
        job_type="direct_translation", input_srt_path=input_srt_path, input_filename=input_filename,
        target_languages=target_languages, output_formats=output_formats or ["srt", "vtt", "webvtt"],
        source_language=source_language, priority=priority, max_retries=max_retries,
        glossary_entities=glossary_entities, tvdb_id=tvdb_id,
    )
    session.add(job)
    session.flush()
    return job


def claim_next_job(session: Session, worker_id: str) -> Job | None:
    candidate_id = session.execute(
        select(Job.id).where(Job.status == "queued").order_by(Job.priority.desc(), Job.created_at.asc()).limit(1)
    ).scalar_one_or_none()
    if candidate_id is None:
        return None

    result = session.execute(
        update(Job).where(Job.id == candidate_id, Job.status == "queued")
        # error_message reset here: it records why the *previous* attempt stopped (a
        # crash, an orphaned-job recovery, a real failure that still had retries left) --
        # once a fresh attempt actually starts, that reason is no longer current and must
        # not linger and render as if the job were failing right now (see JobDetailPage).
        .values(status="running", locked_by=worker_id, locked_at=_now(), current_stage="starting", error_message=None)
    )
    session.commit()
    if result.rowcount == 0:
        return None  # another worker won the race for this exact row; caller polls again
    return session.get(Job, candidate_id)


def mark_job_progress(session: Session, job_id: str, *, stage: str, progress_pct: float) -> None:
    session.execute(
        update(Job).where(Job.id == job_id).values(current_stage=stage, progress_pct=progress_pct)
    )
    session.commit()


def mark_job_done(session: Session, job_id: str) -> None:
    session.execute(update(Job).where(Job.id == job_id).values(status="done", progress_pct=100.0, current_stage="done"))
    session.commit()


def mark_job_failed_or_retry(session: Session, job_id: str, error_message: str) -> str:
    """Returns the resulting status ('queued' if retried, 'failed' if retries exhausted)."""
    job = session.get(Job, job_id)
    assert job is not None
    if job.retry_count < job.max_retries:
        session.execute(
            update(Job).where(Job.id == job_id)
            .values(status="queued", retry_count=job.retry_count + 1, error_message=error_message,
                    locked_by=None, locked_at=None)
        )
        session.commit()
        return "queued"
    session.execute(
        update(Job).where(Job.id == job_id).values(status="failed", error_message=error_message)
    )
    session.commit()
    return "failed"


def cancel_job(session: Session, job_id: str) -> bool:
    result = session.execute(
        update(Job).where(Job.id == job_id, Job.status.in_(("queued", "running", "paused", "needs_decision")))
        .values(status="canceled", canceled_at=_now())
    )
    session.commit()
    return result.rowcount > 0


def pause_job(session: Session, job_id: str) -> bool:
    result = session.execute(update(Job).where(Job.id == job_id, Job.status == "queued").values(status="paused"))
    session.commit()
    return result.rowcount > 0


def resume_job(session: Session, job_id: str) -> bool:
    result = session.execute(update(Job).where(Job.id == job_id, Job.status == "paused").values(status="queued"))
    session.commit()
    return result.rowcount > 0


def reorder_job(session: Session, job_id: str, priority: int) -> bool:
    result = session.execute(update(Job).where(Job.id == job_id, Job.status == "queued").values(priority=priority))
    session.commit()
    return result.rowcount > 0


def is_canceled(session: Session, job_id: str) -> bool:
    status = session.execute(select(Job.status).where(Job.id == job_id)).scalar_one_or_none()
    return status == "canceled"


def recover_orphaned_jobs(session: Session) -> list[str]:
    """Called once at process startup, before any worker thread starts claiming work.

    A job left in `status="running"` from a *previous* process (this container restarted,
    was OOM-killed, etc.) has no thread anywhere actually executing it — the worker loop
    that owned it died with that process. Left alone it stays "running" forever and, worse,
    permanently wedges any GPU slot it held (`gpu_slots.held_by` is never cleared), so every
    future job queues behind a slot that will never free until `GPU_SLOT_WAIT_TIMEOUT_S`
    expires it. Route each one through the normal retry/fail path exactly as if its worker
    thread had crashed, and free any GPU slot it was holding. Returns the recovered job ids.
    """
    session.execute(update(GpuSlot).where(GpuSlot.held_by.in_(
        select(Job.id).where(Job.status == "running")
    )).values(held_by=None, acquired_at=None))
    session.commit()

    orphaned_ids = list(session.execute(select(Job.id).where(Job.status == "running")).scalars())
    for job_id in orphaned_ids:
        mark_job_failed_or_retry(
            session, job_id,
            "Orphaned: no worker process was running this job when the API process started "
            "(likely a container restart or crash mid-job).",
        )
    return orphaned_ids


# --- GPU slot semaphore ---

def init_gpu_slots(session: Session, total_slots: int) -> None:
    existing = {row.slot_index for row in session.execute(select(GpuSlot)).scalars()}
    for i in range(total_slots):
        if i not in existing:
            session.add(GpuSlot(slot_index=i, held_by=None))
    # Shrinking (fewer slots than exist) intentionally leaves extra rows in place rather
    # than deleting a slot a running job might currently hold.
    session.commit()


def try_acquire_gpu_slot(session: Session, job_id: str) -> bool:
    result = session.execute(
        text(
            "UPDATE gpu_slots SET held_by = :job_id, acquired_at = :now "
            "WHERE slot_index = (SELECT slot_index FROM gpu_slots WHERE held_by IS NULL LIMIT 1)"
        ),
        {"job_id": job_id, "now": _now().isoformat()},
    )
    session.commit()
    return result.rowcount > 0


def release_gpu_slot(session: Session, job_id: str) -> None:
    session.execute(update(GpuSlot).where(GpuSlot.held_by == job_id).values(held_by=None, acquired_at=None))
    session.commit()
