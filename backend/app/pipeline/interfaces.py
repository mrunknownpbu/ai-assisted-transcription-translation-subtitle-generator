"""Typed contracts between pipeline stages.

Hard isolation rule: a stage's `run()` takes only the typed dataclass(es) produced by the
stage(s) before it — never a DB session, never another stage's private state. This is what
makes every stage independently unit-testable with hand-built fixtures and lets a stage's
internals change freely as long as its Input/Output shape holds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, TypeVar


# ---------------------------------------------------------------------------
# Stage 1: Media Inspection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AudioStreamInfo:
    stream_index: int
    codec: str
    channels: int
    sample_rate: int
    duration_s: float
    stream_hash: str
    embedded_language_tag: str | None  # evidence only — never used as transcription input
    bit_rate: int | None = None
    channel_layout: str | None = None


@dataclass(frozen=True)
class MediaInspectionResult:
    source_path: str
    filename: str
    container_format: str
    size_bytes: int
    file_hash: str
    duration_s: float
    audio_streams: list[AudioStreamInfo]
    raw_ffprobe_json: dict
    existing_subtitle_streams: list[dict] = field(default_factory=list)  # tagged "evidence" only, never ground truth


# ---------------------------------------------------------------------------
# Stage 2: Audio Stream Selection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StreamRankingScore:
    stream_index: int
    dialogue_score: float  # 0..1, higher = more likely dialogue
    breakdown: dict  # e.g. {"speech_band_energy_ratio": .., "voiced_frame_ratio": .., "dynamic_range": ..}


@dataclass(frozen=True)
class StreamSelectionResult:
    rankings: list[StreamRankingScore]  # sorted best-first
    selected_stream_index: int
    selected_by: str  # 'auto' | 'manual'


# ---------------------------------------------------------------------------
# Stage 3: Language Detection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LanguageDetectionResult:
    detected_language: str  # ISO 639-1 where possible
    confidence: float
    method: str
    candidates: list[tuple[str, float]] = field(default_factory=list)  # runner-up languages + scores
    overridden: bool = False
    effective_language: str = ""  # what actually gets used downstream (override wins if set)

    def __post_init__(self):
        if not self.effective_language:
            object.__setattr__(self, "effective_language", self.detected_language)


# ---------------------------------------------------------------------------
# Stage 4/5: ASR + Canonical Transcript
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ASRWord:
    word: str
    start: float
    end: float
    confidence: float


@dataclass(frozen=True)
class ASRSegment:
    segment_id: str
    start: float
    end: float
    text: str
    words: list[ASRWord]
    avg_confidence: float
    no_speech_prob: float | None = None  # engine's own silence/hallucination signal, if it exposes one
    compression_ratio: float | None = None  # gzip-compression ratio of decoded text; high == degenerate/looping decode
    avg_logprob: float | None = None  # engine's own average token log-probability for this segment


@dataclass(frozen=True)
class ASRResult:
    engine: str
    model_version: str
    language: str
    segments: list[ASRSegment]


@dataclass
class CanonicalSegment:
    """Mutable (unlike the frozen ASR types) because hallucination defense and
    normalization annotate it in place rather than rebuilding the whole transcript."""
    segment_id: str
    start: float
    end: float
    text: str
    normalized_text: str
    words: list[ASRWord]
    avg_confidence: float
    no_speech_prob: float | None = None
    compression_ratio: float | None = None
    avg_logprob: float | None = None
    suppressed: bool = False
    suppression_reason: str | None = None
    hallucination_score: float = 0.0


@dataclass
class CanonicalTranscript:
    segments: list[CanonicalSegment]
    provenance: dict  # stream id, engine, model_version, language, detection method, hardware profile


# ---------------------------------------------------------------------------
# Stage 6: Hallucination Defense
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VoiceActivitySpan:
    start: float
    end: float


@dataclass(frozen=True)
class SuppressionDecision:
    segment_id: str
    decision: str  # 'suppressed' | 'kept_marginal'
    reason: str
    method: str
    threshold: dict


# ---------------------------------------------------------------------------
# Stage 8: Translation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceChunk:
    """A group of canonical segments merged by pause-gap, translated as one unit for
    context. `word_span` is the (min_start, max_end) across every word in the chunk —
    the only thing timing projection is ever allowed to read from."""
    chunk_id: str
    source_text: str
    word_span: tuple[float, float]
    source_segment_ids: list[str]


@dataclass(frozen=True)
class TranslatedChunk:
    chunk_id: str
    target_language: str
    source_text: str
    translated_text: str
    engine: str
    model_version: str
    word_span: tuple[float, float]


# ---------------------------------------------------------------------------
# Stage 9/10: Target Segmentation + Timing Projection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TargetCueDraft:
    """Stage 9's output: cue TEXT and which source chunk(s) it maps to, with no timing
    yet — duration depends on time budget apportionment, which is Stage 10's job alone."""
    draft_id: str
    text: str
    source_chunk_ids: list[str]  # one id = a chunk split into this cue; multiple = chunks merged into it
    char_share_of_chunks: dict[str, float]  # chunk_id -> fraction of that chunk's duration this cue takes


@dataclass(frozen=True)
class TargetCue:
    cue_id: str
    start: float
    end: float
    text: str
    source_chunk_ids: list[str]
    char_share_of_chunk: float  # fraction of the (first/primary) source chunk's text this cue represents


# ---------------------------------------------------------------------------
# Stage 11: QC & Output
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QcFinding:
    check: str
    passed: bool
    reason: str
    cue_id: str | None = None


@dataclass(frozen=True)
class QcReport:
    stage: str
    target_language: str | None
    passed: bool
    findings: list[QcFinding]


# ---------------------------------------------------------------------------
# Shared engine-fallback bookkeeping (ASR and translation both use this shape)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EngineAttempt:
    engine: str
    outcome: str  # 'skipped_unavailable' | 'failed' | 'succeeded'
    detail: str | None = None


TIn = TypeVar("TIn")
TOut = TypeVar("TOut")


class Stage(Protocol[TIn, TOut]):
    name: str

    def run(self, data: TIn) -> TOut: ...
