from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session, joinedload

from app.api.schemas import CreateJobRequest, ExistingOutputDecisionRequest, JobOut, OutputOut, ReorderRequest
from app.config import Settings, get_settings
from app.db.models import AudioStream, ExistingOutputDecision, Job, JobStageRun, MediaFile, Output, QcReport, Suppression
from app.db.session import get_session
from app.jobs.queue import cancel_job, enqueue_direct_translation_job, enqueue_job, pause_job, reorder_job, resume_job
from app.pipeline.stage8_translation.glossary_profile import load_profile_entities
from app.pipeline.stage8_translation.tvdb_client import glossary_from_characters

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _resolve_glossary_entities(
    explicit_entities: list[dict] | None, tvdb_id: int | None, glossary_profile_dir,
) -> list[dict] | None:
    """Shared by both job-creation routes. Three layers, lowest to highest precedence:

    1. Live TVDB cast-list auto-population (zero setup, but a real show's TVDB entry is
       routinely incomplete -- confirmed case: 'Evren', a recurring character in Sen Çal
       Kapımı / Love Is In The Air tvdb-383383, mistranslated as "Universe" throughout an
       episode because TVDB's own cast list for that series never included him at all).
    2. An optional local glossary-profile YAML layer (see glossary_profile.py) -- a
       human-curated correction/supplement file per series, ported from a sibling
       project's design specifically because its real data for this same show already
       documented that exact "Evren" -> "Mr. Universe" failure, plus others TVDB alone
       can't know about (nicknames the show's own dialogue actually uses instead of a
       character's full name).
    3. An explicit per-job glossary, the most specific request-time override.

    Each layer overrides a same-named canonical entry from an earlier one; without this
    merge, supplying just one missing name at any layer would mean retyping every other
    name by hand and losing auto-population for the rest. Both TVDB and profile lookups
    are best-effort (glossary_from_characters() never raises; a missing/unreadable profile
    directory just yields no entities) -- neither can fail job creation."""
    auto_entities = glossary_from_characters(tvdb_id) if tvdb_id is not None else []
    profile_entities = load_profile_entities(glossary_profile_dir, tvdb_id) if glossary_profile_dir else []

    merged: dict[str, dict] = {}
    for e in auto_entities:
        merged[e.canonical] = {"canonical": e.canonical, "surface_forms": e.surface_forms}
    for e in profile_entities:
        merged[e.canonical] = {"canonical": e.canonical, "surface_forms": e.surface_forms}
    for entry in explicit_entities or []:
        merged[entry["canonical"]] = entry
    return list(merged.values()) or None


@router.post("", response_model=JobOut)
def create_job(body: CreateJobRequest, session: Session = Depends(get_session), settings: Settings = Depends(get_settings)):
    media = session.get(MediaFile, body.media_file_id)
    stream = session.get(AudioStream, body.audio_stream_id)
    if media is None or stream is None or stream.media_file_id != body.media_file_id:
        raise HTTPException(404, "Media file or audio stream not found")

    # A TVDB id explicitly passed on the request wins; otherwise fall back to the one
    # auto-extracted from the library's "{tvdb-XXXXX}" folder convention at registration
    # time (see media.py::_register_inspected_media), so a file registered straight from a
    # real media library gets character-name glossary protection with zero manual setup.
    tvdb_id = body.tvdb_id if body.tvdb_id is not None else media.tvdb_id

    explicit_entities = [e.model_dump() for e in body.glossary_entities] if body.glossary_entities else None
    glossary_entities = _resolve_glossary_entities(explicit_entities, tvdb_id, settings.glossary_profile_dir)

    existing_outputs = session.query(Output).join(Job).filter(Job.media_file_id == body.media_file_id).count()
    job = enqueue_job(
        session, media_file_id=body.media_file_id, audio_stream_id=body.audio_stream_id,
        target_languages=body.target_languages, output_formats=body.output_formats,
        source_language=body.source_language, priority=body.priority,
        glossary_entities=glossary_entities, tvdb_id=tvdb_id,
    )
    if existing_outputs > 0:
        # Existing AI output for this media already exists. Distinct from 'paused' (a
        # user-initiated hold on a queued job) so the UI can tell the two apart and show
        # the KEEP/REPLACE prompt instead of a plain resume button. The worker's claim
        # query only ever selects status='queued', so this job sits inert until decided.
        job.status = "needs_decision"
        session.commit()
    return job


@router.post("/direct-translation", response_model=JobOut)
def create_direct_translation_job(
    file: UploadFile, target_languages: str = Form(...), output_formats: str = Form("srt,vtt,webvtt"),
    source_language: str | None = Form(None), glossary_entities: str | None = Form(None),
    tvdb_id: int | None = Form(None), priority: int = Form(0),
    session: Session = Depends(get_session), settings: Settings = Depends(get_settings),
):
    """Translates a user-supplied .srt file directly — no audio, no ASR. A clearly separate
    job type from the audio-first pipeline; see `jobs/worker.py`'s `_run_direct_translation`
    and its provenance note ("not verified against audio")."""
    if not (file.filename or "").lower().endswith(".srt"):
        raise HTTPException(422, "Only .srt files are accepted for direct translation")

    dest_path = Path(settings.media_dir) / f"{uuid.uuid4().hex}_{file.filename}"
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    explicit_glossary = json.loads(glossary_entities) if glossary_entities else None
    parsed_glossary = _resolve_glossary_entities(explicit_glossary, tvdb_id, settings.glossary_profile_dir)

    job = enqueue_direct_translation_job(
        session, input_srt_path=str(dest_path), input_filename=file.filename or dest_path.name,
        target_languages=[t.strip() for t in target_languages.split(",") if t.strip()],
        output_formats=[f.strip() for f in output_formats.split(",") if f.strip()],
        source_language=source_language, priority=priority,
        glossary_entities=parsed_glossary, tvdb_id=tvdb_id,
    )
    return job


@router.get("", response_model=list[JobOut])
def list_jobs(status: str | None = None, session: Session = Depends(get_session)):
    query = session.query(Job).options(joinedload(Job.media_file)).order_by(Job.priority.desc(), Job.created_at.asc())
    if status:
        query = query.filter(Job.status == status)
    return query.all()


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, session: Session = Depends(get_session)):
    job = session.query(Job).options(joinedload(Job.media_file)).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel(job_id: str, session: Session = Depends(get_session)):
    if not cancel_job(session, job_id):
        raise HTTPException(409, "Job cannot be canceled from its current state")
    return session.get(Job, job_id)


@router.post("/{job_id}/pause", response_model=JobOut)
def pause(job_id: str, session: Session = Depends(get_session)):
    if not pause_job(session, job_id):
        raise HTTPException(409, "Only a queued job can be paused")
    return session.get(Job, job_id)


@router.post("/{job_id}/resume", response_model=JobOut)
def resume(job_id: str, session: Session = Depends(get_session)):
    if not resume_job(session, job_id):
        raise HTTPException(409, "Only a paused job can be resumed")
    return session.get(Job, job_id)


@router.post("/{job_id}/retry", response_model=JobOut)
def retry(job_id: str, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status != "failed":
        raise HTTPException(409, "Only a failed job can be retried")
    job.status = "queued"
    job.retry_count = 0
    job.error_message = None
    session.commit()
    return job


@router.patch("/{job_id}/priority", response_model=JobOut)
def reorder(job_id: str, body: ReorderRequest, session: Session = Depends(get_session)):
    if not reorder_job(session, job_id, body.priority):
        raise HTTPException(409, "Only a queued job can be reordered")
    return session.get(Job, job_id)


@router.post("/{job_id}/existing-output-decision", response_model=JobOut)
def decide_existing_output(job_id: str, body: ExistingOutputDecisionRequest, session: Session = Depends(get_session)):
    if body.decision not in ("keep", "replace"):
        raise HTTPException(422, "decision must be 'keep' or 'replace'")
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status != "needs_decision":
        raise HTTPException(409, "This job has no pending KEEP/REPLACE decision")

    session.add(ExistingOutputDecision(job_id=job_id, decision=body.decision))
    if body.decision == "replace":
        job.status = "queued"
    else:
        job.status = "canceled"
        job.canceled_at = job.updated_at
    session.commit()
    return job


@router.get("/{job_id}/logs")
def get_job_logs(job_id: str, session: Session = Depends(get_session)):
    stages = session.query(JobStageRun).filter_by(job_id=job_id).order_by(JobStageRun.started_at).all()
    suppressions = session.query(Suppression).filter_by(job_id=job_id).all()
    qc_reports = session.query(QcReport).filter_by(job_id=job_id).all()
    return {
        "stages": [
            {"stage_name": s.stage_name, "status": s.status, "started_at": s.started_at,
             "finished_at": s.finished_at, "log": s.log, "provenance": s.provenance_json}
            for s in stages
        ],
        "suppressions": [
            {"segment_id": s.segment_id, "reason": s.reason, "method": s.method,
             "threshold": s.threshold_json, "decision": s.decision}
            for s in suppressions
        ],
        "qc_reports": [
            {"target_language": q.target_language, "passed": q.passed, "findings": q.reasons_json}
            for q in qc_reports
        ],
    }


@router.get("/{job_id}/outputs", response_model=list[OutputOut])
def get_job_outputs(job_id: str, session: Session = Depends(get_session)):
    return session.query(Output).filter_by(job_id=job_id).all()
