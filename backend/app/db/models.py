"""SQLAlchemy models — the persistent job/state store described in the architecture plan.

Design notes:
- `audio_streams.stream_hash` is the cache key for everything downstream (language
  detection, ASR), NOT the media filename — a renamed or re-muxed file with the same
  actual audio reuses cached results; a file with the same name but different audio does not.
- Nothing pipeline-related is ever deleted on suppression/failure; rows are append-only
  logs (`suppressions`, `job_stage_runs`, `qc_reports`) so every decision stays auditable.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class HardwareProfileRow(Base):
    __tablename__ = "hardware_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    detected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    vendor: Mapped[str] = mapped_column(String(16))
    gpu_count: Mapped[int] = mapped_column(Integer, default=0)
    gpu_names_json: Mapped[dict] = mapped_column(JSON, default=list)
    total_vram_mb: Mapped[int] = mapped_column(Integer, default=0)
    cpu_cores: Mapped[int] = mapped_column(Integer, default=0)
    total_ram_mb: Mapped[int] = mapped_column(Integer, default=0)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    process_role: Mapped[str] = mapped_column(String(16), default="api")  # api | worker


class MediaFile(Base):
    __tablename__ = "media_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    original_path: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(String(512))
    container_format: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    file_hash: Mapped[str] = mapped_column(String(64))  # sha256 of source bytes, for read-only-safety verification
    inspected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    raw_ffprobe_json: Mapped[dict] = mapped_column(JSON)
    # Auto-extracted from a library path's "{tvdb-XXXXX}" folder segment (the convention
    # Sonarr/arr-stack media managers already use) at registration time; None for uploads
    # or library files with no such segment. Lets job creation auto-populate a translation
    # glossary from the series' real cast list with zero manual entry.
    tvdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    audio_streams: Mapped[list["AudioStream"]] = relationship(back_populates="media_file", cascade="all, delete-orphan")


class AudioStream(Base):
    __tablename__ = "audio_streams"
    __table_args__ = (UniqueConstraint("media_file_id", "stream_index", name="uq_media_stream_index"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    media_file_id: Mapped[str] = mapped_column(ForeignKey("media_files.id"))
    stream_index: Mapped[int] = mapped_column(Integer)
    codec: Mapped[str] = mapped_column(String(64))
    channels: Mapped[int] = mapped_column(Integer)
    sample_rate: Mapped[int] = mapped_column(Integer)
    duration_s: Mapped[float] = mapped_column(Float)
    # cache identity: hash of codec+channels+sample_rate+duration+a content sample, never the filename
    stream_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedded_language_tag: Mapped[str | None] = mapped_column(String(16), nullable=True)  # evidence only, never ground truth
    dialogue_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    dialogue_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dialogue_score_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    selected_by: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 'auto' | 'manual'
    selected_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    media_file: Mapped[MediaFile] = relationship(back_populates="audio_streams")


class LanguageDetection(Base):
    __tablename__ = "language_detections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    audio_stream_id: Mapped[str] = mapped_column(ForeignKey("audio_streams.id"), index=True)
    stream_hash: Mapped[str] = mapped_column(String(64), index=True)  # cache lookup key
    detected_language: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(64))
    overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    override_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # 'audio_pipeline' (default): the full 11-stage pipeline, requires media_file_id/audio_stream_id.
    # 'direct_translation': translates a user-supplied .srt directly, requires input_srt_path instead —
    # never claims the subtitle text came from audio; see pipeline/direct_translation/.
    job_type: Mapped[str] = mapped_column(String(32), default="audio_pipeline")
    media_file_id: Mapped[str | None] = mapped_column(ForeignKey("media_files.id"), nullable=True)
    audio_stream_id: Mapped[str | None] = mapped_column(ForeignKey("audio_streams.id"), nullable=True)
    input_srt_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # direct_translation only
    input_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)  # direct_translation only
    source_language: Mapped[str | None] = mapped_column(String(16), nullable=True)  # None = auto-detect
    target_languages: Mapped[list] = mapped_column(JSON, default=list)  # user-specified, dynamic, any length
    output_formats: Mapped[list] = mapped_column(JSON, default=lambda: ["srt", "vtt", "webvtt"])
    # Optional entity/glossary protection for translation (Stage 8): [{"canonical": str, "aliases": [str, ...]}].
    glossary_entities: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tvdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # optional: auto-populate glossary from TVDB cast
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # queued | running | paused | needs_decision | canceled | failed | done
    current_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress_pct: Mapped[float] = mapped_column(Float, default=0.0)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # higher runs first
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)  # worker id holding the claim
    locked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    hardware_profile_id: Mapped[int | None] = mapped_column(ForeignKey("hardware_profiles.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    canceled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    media_file: Mapped["MediaFile | None"] = relationship()

    @property
    def display_filename(self) -> str | None:
        """input_filename is only ever populated for direct_translation jobs (there's no
        other file to name); for audio_pipeline jobs the actual source filename lives on
        the related MediaFile row instead. Every UI list/detail view wants one name
        regardless of job type, so resolve it here rather than duplicating this fallback
        in every caller."""
        if self.input_filename:
            return self.input_filename
        return self.media_file.filename if self.media_file else None


class JobStageRun(Base):
    __tablename__ = "job_stage_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    stage_name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # running | done | failed | skipped
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    log: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    audio_stream_id: Mapped[str] = mapped_column(ForeignKey("audio_streams.id"))
    engine: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(128))
    language: Mapped[str] = mapped_column(String(16))
    raw_json_path: Mapped[str] = mapped_column(Text)  # canonical transcript object, stored on disk (can be large)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Suppression(Base):
    __tablename__ = "suppressions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    segment_id: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(128))
    method: Mapped[str] = mapped_column(String(64))
    threshold_json: Mapped[dict] = mapped_column(JSON)
    raw_text: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(String(16))  # 'suppressed' | 'kept_marginal'
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Translation(Base):
    __tablename__ = "translations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    target_language: Mapped[str] = mapped_column(String(16))
    engine: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(128))
    segments_json_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class QcReport(Base):
    __tablename__ = "qc_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    target_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    reasons_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Output(Base):
    __tablename__ = "outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    format: Mapped[str] = mapped_column(String(16))  # srt | vtt | webvtt | burned_in
    target_language: Mapped[str] = mapped_column(String(16))
    file_path: Mapped[str] = mapped_column(Text)
    provenance_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ExistingOutputDecision(Base):
    __tablename__ = "existing_output_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    decision: Mapped[str] = mapped_column(String(16))  # 'keep' | 'replace'
    decided_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GpuSlot(Base):
    """DB-backed GPU concurrency semaphore: one row per usable GPU slot (gpu_count *
    max_concurrent_gpu_jobs), acquired/released via atomic UPDATE so it coordinates
    correctly across worker processes/containers, not just threads within one process."""
    __tablename__ = "gpu_slots"

    slot_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    held_by: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    acquired_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvaluationReport(Base):
    """Translation-accuracy score for one completed job's output, measured against a real
    reference subtitle track (embedded stream or sidecar file) for the same episode --
    never generated, always something a human actually produced. See app/evaluation/."""
    __tablename__ = "evaluation_reports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    # 'translation' (default): scores translated output against a target-language reference.
    # 'transcription': scores our own ASR transcript against a source-language reference --
    # e.g. a human-made embedded subtitle in the original language, isolating ASR accuracy
    # from translation accuracy.
    mode: Mapped[str] = mapped_column(String(16), default="translation")
    target_language: Mapped[str] = mapped_column(String(64))
    reference_source: Mapped[str] = mapped_column(Text)  # sidecar path, or "embedded:<stream_index>"
    cue_count: Mapped[int] = mapped_column(Integer)
    matched_count: Mapped[int] = mapped_column(Integer)
    coverage: Mapped[float] = mapped_column(Float)
    mean_chrf: Mapped[float] = mapped_column(Float)
    mean_word_f1: Mapped[float] = mapped_column(Float)
    per_cue_json: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
