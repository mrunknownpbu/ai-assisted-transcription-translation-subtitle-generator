"""Pydantic request/response models for the HTTP API. Kept separate from the SQLAlchemy
models in `db/models.py` so the wire format can evolve independently of storage."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class AudioStreamOut(BaseModel):
    id: str
    stream_index: int
    codec: str
    channels: int
    sample_rate: int
    duration_s: float
    embedded_language_tag: str | None
    dialogue_score: float | None
    dialogue_rank: int | None
    dialogue_score_breakdown: dict | None
    selected_by: str | None

    model_config = {"from_attributes": True}


class MediaFileOut(BaseModel):
    id: str
    filename: str
    container_format: str
    size_bytes: int
    inspected_at: dt.datetime
    audio_streams: list[AudioStreamOut]
    tvdb_id: int | None = None

    model_config = {"from_attributes": True}


class SelectStreamRequest(BaseModel):
    stream_index: int


class RegisterMediaRequest(BaseModel):
    path: str  # relative to the configured library root


class LibraryEntryOut(BaseModel):
    name: str
    path: str  # relative to the library root — pass straight back into browse/register
    is_dir: bool


class LanguageDetectionOut(BaseModel):
    detected_language: str
    confidence: float
    method: str
    candidates: list[tuple[str, float]]


class GlossaryEntityIn(BaseModel):
    canonical: str
    aliases: list[str] = Field(default_factory=list)


class CreateJobRequest(BaseModel):
    media_file_id: str
    audio_stream_id: str
    source_language: str | None = None  # None = auto-detect
    target_languages: list[str] = Field(min_length=1)
    output_formats: list[str] = Field(default_factory=lambda: ["srt", "vtt", "webvtt"])
    priority: int = 0
    # Optional entity/glossary protection for translation (Stage 8) — a job with neither
    # field set behaves identically to before this feature existed.
    glossary_entities: list[GlossaryEntityIn] | None = None
    tvdb_id: int | None = None


class JobOut(BaseModel):
    id: str
    job_type: str
    media_file_id: str | None
    audio_stream_id: str | None
    input_filename: str | None
    display_filename: str | None
    source_language: str | None
    target_languages: list[str]
    output_formats: list[str]
    status: str
    current_stage: str | None
    progress_pct: float
    priority: int
    retry_count: int
    max_retries: int
    error_message: str | None
    created_at: dt.datetime
    updated_at: dt.datetime

    model_config = {"from_attributes": True}


class ReorderRequest(BaseModel):
    priority: int


class ExistingOutputDecisionRequest(BaseModel):
    decision: str  # 'keep' | 'replace'


class OutputOut(BaseModel):
    id: int
    format: str
    target_language: str
    file_path: str
    provenance_json: dict
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class HardwareProfileOut(BaseModel):
    vendor: str
    gpu_count: int
    gpus: list[dict]
    cpu_cores: int
    total_ram_mb: int
    fallback_reason: str | None


class StorageOut(BaseModel):
    total_bytes: int
    used_bytes: int
    free_bytes: int


class SettingsOut(BaseModel):
    whisper_model_size: str
    nllb_model_name: str
    max_concurrent_gpu_jobs: int
    library_configured: bool
    tvdb_configured: bool


class EmbeddedSubtitleStreamOut(BaseModel):
    index: int
    language: str | None
    codec_name: str | None


class SiblingSubtitleFileOut(BaseModel):
    name: str
    path: str  # relative to the configured library root -- feeds straight into EvaluateJobRequest.reference_srt_path


class ReferenceOptionsOut(BaseModel):
    embedded_subtitle_streams: list[EmbeddedSubtitleStreamOut]
    sibling_subtitle_files: list[SiblingSubtitleFileOut]


class EvaluateJobRequest(BaseModel):
    # 'translation' (default): score translated output against a target-language reference.
    # 'transcription': score our own ASR transcript against a source-language reference
    # (e.g. a human-made embedded subtitle in the original language) -- isolates ASR
    # accuracy from translation accuracy.
    mode: str = "translation"
    target_language: str | None = None  # translation mode only; None = the job's only/first target
    # Exactly one of these three must be set:
    # - a sidecar reference file next to the source media (path relative to the configured
    #   library root, same convention as media.register)
    # - an embedded subtitle stream index on the job's own source media file
    # - a file in the configured external reference archive (path relative to
    #   settings.reference_dir, same convention as library browsing but for a folder of
    #   real reference translations kept outside the media library -- see references.py)
    reference_srt_path: str | None = None
    embedded_stream_index: int | None = None
    reference_archive_path: str | None = None


class CueComparisonOut(BaseModel):
    hypothesis_index: int
    start: float
    end: float
    hypothesis_text: str
    reference_text: str
    chrf: float
    word_f1: float


class EvaluationReportOut(BaseModel):
    id: str
    job_id: str
    mode: str
    target_language: str
    reference_source: str
    cue_count: int
    matched_count: int
    coverage: float
    mean_chrf: float
    mean_word_f1: float
    per_cue: list[CueComparisonOut]
    created_at: dt.datetime

    model_config = {"from_attributes": True}
