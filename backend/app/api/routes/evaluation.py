"""Accuracy evaluation for a completed job, in two modes:

- 'translation' (default): scores translated output against a target-language reference.
- 'transcription': scores our own ASR transcript against a source-language reference (e.g.
  a human-made embedded subtitle in the original language) -- isolates ASR accuracy from
  translation accuracy, so a low translation score can be attributed to the right stage.

Both modes compare against a real reference subtitle already present for the episode (an
embedded mkv stream or a sidecar .srt file) -- never a generated reference. See
app/evaluation/ for the scoring itself; this route only wires job/media/transcript lookup
and persistence around it.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.routes.media import resolve_library_path
from app.api.routes.references import resolve_reference_path
from app.api.schemas import CueComparisonOut, EvaluateJobRequest, EvaluationReportOut
from app.config import Settings, get_settings
from app.db.models import EvaluationReport, Job, MediaFile, Output, Transcript
from app.db.session import get_session
from app.evaluation.metrics import evaluate_cues
from app.evaluation.reference_extraction import (
    ReferenceExtractionError, extract_embedded_subtitle_text, read_sidecar_subtitle_text,
)
from app.evaluation.transcript_cues import canonical_transcript_to_cues
from app.pipeline.stage11_qc_output.formatters.srt import parse_srt

router = APIRouter(prefix="/api/jobs", tags=["evaluation"])


def _report_to_out(report: EvaluationReport) -> EvaluationReportOut:
    return EvaluationReportOut(
        id=report.id, job_id=report.job_id, mode=report.mode, target_language=report.target_language,
        reference_source=report.reference_source, cue_count=report.cue_count,
        matched_count=report.matched_count, coverage=report.coverage, mean_chrf=report.mean_chrf,
        mean_word_f1=report.mean_word_f1, per_cue=[CueComparisonOut(**c) for c in report.per_cue_json],
        created_at=report.created_at,
    )


def _load_hypothesis_cues(job: Job, body: EvaluateJobRequest, session: Session) -> tuple[list, str]:
    """Returns (hypothesis_cues, report_language)."""
    if body.mode == "transcription":
        transcript = session.query(Transcript).filter_by(job_id=job.id).order_by(Transcript.created_at.desc()).first()
        if transcript is None:
            raise HTTPException(404, f"No transcript found for job {job.id}")
        return canonical_transcript_to_cues(transcript.raw_json_path), transcript.language

    target_language = body.target_language or job.target_languages[0]
    output = (
        session.query(Output)
        .filter_by(job_id=job.id, target_language=target_language, format="srt")
        .order_by(Output.created_at.desc())
        .first()
    )
    if output is None:
        raise HTTPException(404, f"No SRT output found for job {job.id} / target language '{target_language}'")
    hypothesis_text = Path(output.file_path).read_text(encoding="utf-8-sig")
    return parse_srt(hypothesis_text), target_language


@router.post("/{job_id}/evaluate", response_model=EvaluationReportOut)
def evaluate_job(
    job_id: str, body: EvaluateJobRequest,
    session: Session = Depends(get_session), settings: Settings = Depends(get_settings),
):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status != "done":
        raise HTTPException(409, f"Job must be completed before evaluating (status is '{job.status}')")
    if body.mode not in ("translation", "transcription"):
        raise HTTPException(422, "mode must be 'translation' or 'transcription'")

    reference_options_given = sum([
        bool(body.reference_srt_path), body.embedded_stream_index is not None, bool(body.reference_archive_path),
    ])
    if reference_options_given != 1:
        raise HTTPException(
            422, "Provide exactly one of reference_srt_path, embedded_stream_index, or reference_archive_path",
        )

    hypothesis_cues, report_language = _load_hypothesis_cues(job, body, session)

    try:
        if body.reference_srt_path:
            reference_path = resolve_library_path(settings, body.reference_srt_path)
            reference_text = read_sidecar_subtitle_text(reference_path)
            reference_source = str(reference_path)
        elif body.reference_archive_path:
            reference_path = resolve_reference_path(settings, body.reference_archive_path)
            reference_text = read_sidecar_subtitle_text(reference_path)
            reference_source = f"archive:{reference_path}"
        else:
            if job.media_file_id is None:
                raise HTTPException(422, "This job has no source media file to extract an embedded stream from")
            media = session.get(MediaFile, job.media_file_id)
            if media is None:
                raise HTTPException(404, "Source media file not found")
            reference_text = extract_embedded_subtitle_text(media.original_path, body.embedded_stream_index)
            reference_source = f"embedded:{body.embedded_stream_index}"
    except ReferenceExtractionError as exc:
        raise HTTPException(422, str(exc)) from exc

    reference_cues = parse_srt(reference_text)
    if not reference_cues:
        raise HTTPException(422, "Reference subtitle track parsed to zero cues -- wrong stream/path?")

    result = evaluate_cues(hypothesis_cues, reference_cues)

    report = EvaluationReport(
        job_id=job_id, mode=body.mode, target_language=report_language, reference_source=reference_source,
        cue_count=result.cue_count, matched_count=result.matched_count, coverage=result.coverage,
        mean_chrf=result.mean_chrf, mean_word_f1=result.mean_word_f1,
        per_cue_json=[
            {"hypothesis_index": c.hypothesis_index, "start": c.start, "end": c.end,
             "hypothesis_text": c.hypothesis_text, "reference_text": c.reference_text,
             "chrf": c.chrf, "word_f1": c.word_f1}
            for c in result.comparisons
        ],
    )
    session.add(report)
    session.commit()
    session.refresh(report)
    return _report_to_out(report)


@router.get("/{job_id}/evaluations", response_model=list[EvaluationReportOut])
def list_evaluations(job_id: str, session: Session = Depends(get_session)):
    reports = session.query(EvaluationReport).filter_by(job_id=job_id).order_by(EvaluationReport.created_at.desc()).all()
    return [_report_to_out(r) for r in reports]
