"""Canonical transcript: the internal source of truth for everything the
pipeline knows about a video's speech, from the word up.

Design principle (see ARCHITECTURE.md, "canonical transcript, not SRT"):
SRT is a rendering format with millisecond timestamps and a fixed cue-block
grammar -- it cannot represent word-level confidence, why a boundary exists,
or what corrected what. Every stage after ASR reads and writes this model;
SRT is produced only at the very end, by srt.py, and never read back in.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path


# Languages written without spaces between words -- reconstructing running
# text from word-level ASR tokens (or rejoining split segments) must not
# insert a space for these, unlike every space-delimited language. Real
# defect (Japanese validation, 2026-09-13): faster-whisper's word-level
# timestamps decompose Japanese into individual tokens same as any other
# language, and naively " ".join()-ing them back together produced visibly
# malformed text ("つ か 喋 れ や でも" instead of "つか喋れやでも"), which
# then destabilized NLLB translation into a repetition loop on the garbled
# input. Not exhaustive (e.g. Lao, Khmer, Burmese are also unspaced) --
# covers the languages this project has actually validated so far.
NO_SPACE_LANGUAGES = frozenset({"ja", "zh", "th"})


def join_words(words: list[str], language: str) -> str:
    """Reconstruct running text from word-level tokens, space-delimited or
    not depending on the language. See NO_SPACE_LANGUAGES."""
    if language in NO_SPACE_LANGUAGES:
        return "".join(words)
    return " ".join(words)


class BoundaryReason(str, Enum):
    """Why a segment boundary exists -- decided once, where the acoustic
    evidence actually is (word gaps, punctuation), never re-guessed later
    from rendered timestamps. Mirrors the boundary-provenance design that
    the prior implementation validated works (see MERGEABLE below) --
    retained because the audit found this specific idea sound, not because
    the old code is authoritative."""
    REAL_ACOUSTIC_GAP = "real_acoustic_gap"
    SENTENCE_END = "sentence_end"
    UTTERANCE_END = "utterance_end"          # reserved: needs diarization
    DISPLAY_SPLIT = "display_split"
    MAX_DURATION = "max_duration"
    MAX_LENGTH = "max_length"


# Boundaries that say nothing about the underlying speech -- pure
# consequences of display constraints. Only these are ever safe to cross
# when deciding whether adjacent source segments may become one
# translation/target unit.
MERGEABLE_BOUNDARIES = frozenset({
    BoundaryReason.DISPLAY_SPLIT, BoundaryReason.MAX_DURATION, BoundaryReason.MAX_LENGTH,
})


class CorrectionKind(str, Enum):
    """What kind of automated change was applied to a word/segment's text,
    for audit trail purposes. Every correction is provenance-aware: it
    names its rule, is bounded to specific evidence, and is reversible by
    inspecting `original_text` on the affected Word."""
    HALLUCINATION_SUPPRESSED = "hallucination_suppressed"
    NORMALIZATION = "normalization"           # e.g. petunya/Petunia-class fix


@dataclass
class Correction:
    kind: CorrectionKind
    rule_id: str
    evidence: str
    confidence: float


@dataclass
class Word:
    text: str
    original_text: str          # pre-correction, always preserved
    start: float
    end: float
    probability: float | None = None
    corrections: list[Correction] = field(default_factory=list)

    @property
    def is_corrected(self) -> bool:
        return bool(self.corrections)


@dataclass
class Segment:
    """One ASR decoder segment -- the unit hallucination detection and
    transcription QC reason about, before any display-oriented
    regrouping happens (see segmentation_source.py, which builds
    *cues* from segments+words; a Segment is not a cue)."""
    index: int
    start: float
    end: float
    words: list[Word]
    avg_logprob: float
    no_speech_prob: float
    compression_ratio: float
    boundary_before: BoundaryReason | None = None
    hallucination_score: float = 0.0          # 0 = clean, 1 = certain hallucination
    hallucination_reasons: list[str] = field(default_factory=list)
    suppressed: bool = False                  # True if excluded from output entirely
    language: str = ""                        # see NO_SPACE_LANGUAGES; "" = space-delimited

    @property
    def text(self) -> str:
        return join_words([w.text for w in self.words if w.text], self.language)

    @property
    def is_suspect(self) -> bool:
        return self.hallucination_score > 0 or self.suppressed


@dataclass
class ModelInfo:
    name: str
    version: str
    parameters: dict = field(default_factory=dict)


@dataclass
class CanonicalTranscript:
    media_path: str             # absolute path, identity only -- never re-read from here
    media_hash: str              # see media.content_fingerprint(); cache-key material
    audio_stream_index: int
    language: str
    language_probability: float | None
    asr_model: ModelInfo
    alignment_model: ModelInfo | None
    pipeline_version: str
    # Audio-stream provenance -- see audio_streams.py. embedded_stream_language
    # is the container's own tag on the CHOSEN stream (evidence, never truth;
    # may legitimately disagree with `language` above). stream_selection_mode/
    # reason record how that stream was chosen, independent of how the
    # spoken-language mode (AUTO/MANUAL) was chosen.
    embedded_stream_language: str | None = None
    stream_selection_mode: str = "AUTO"
    stream_selection_reason: str = ""
    created_at: float = field(default_factory=time.time)
    segments: list[Segment] = field(default_factory=list)

    @property
    def words(self) -> list[Word]:
        return [w for seg in self.segments for w in seg.words]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CanonicalTranscript":
        segments = []
        for s in data.get("segments", []):
            words = [Word(text=w["text"], original_text=w.get("original_text", w["text"]),
                          start=w["start"], end=w["end"], probability=w.get("probability"),
                          corrections=[Correction(**c) if not isinstance(c, Correction) else c
                                       for c in w.get("corrections", [])])
                    for w in s.get("words", [])]
            boundary = s.get("boundary_before")
            segments.append(Segment(
                index=s["index"], start=s["start"], end=s["end"], words=words,
                avg_logprob=s.get("avg_logprob", 0.0), no_speech_prob=s.get("no_speech_prob", 0.0),
                compression_ratio=s.get("compression_ratio", 0.0),
                boundary_before=BoundaryReason(boundary) if boundary else None,
                hallucination_score=s.get("hallucination_score", 0.0),
                hallucination_reasons=list(s.get("hallucination_reasons", [])),
                suppressed=s.get("suppressed", False), language=s.get("language", "")))
        asr = data["asr_model"]
        align = data.get("alignment_model")
        return cls(
            media_path=data["media_path"], media_hash=data["media_hash"],
            audio_stream_index=data["audio_stream_index"], language=data["language"],
            language_probability=data.get("language_probability"),
            asr_model=ModelInfo(**asr), alignment_model=ModelInfo(**align) if align else None,
            pipeline_version=data["pipeline_version"],
            embedded_stream_language=data.get("embedded_stream_language"),
            stream_selection_mode=data.get("stream_selection_mode", "AUTO"),
            stream_selection_reason=data.get("stream_selection_reason", ""),
            created_at=data.get("created_at", time.time()),
            segments=segments)

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(target)

    @classmethod
    def load(cls, path: str | Path) -> "CanonicalTranscript":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def cache_key(media_hash: str, audio_stream_index: int, stream_codec: str | None, language: str,
             asr_model: ModelInfo, alignment_model: ModelInfo | None, pipeline_version: str) -> str:
    """Deterministic cache key covering every input that can change the
    transcript's content -- changing any of them must produce a different
    key, or a stale transcript from before a model/pipeline upgrade (or a
    different audio stream of the same file) could be silently reused
    (the exact failure this function exists to prevent).

    `audio_stream_index` + `stream_codec` together identify WHICH track
    was transcribed -- a transcript from MKV+stream#1 must never be
    reused for MKV+stream#2, even if both happen to resolve to the same
    language. `language` is the RESOLVED (actually-transcribed) language,
    e.g. CanonicalTranscript.language -- never the raw request ("auto" or
    a manual code). VAD/decoder configuration is already covered via
    asr_model.parameters (see asr._model_info()), so it isn't a separate
    argument here."""
    payload = json.dumps({
        "media_hash": media_hash, "audio_stream_index": audio_stream_index,
        "stream_codec": stream_codec, "language": language,
        "asr_model": asdict(asr_model),
        "alignment_model": asdict(alignment_model) if alignment_model else None,
        "pipeline_version": pipeline_version,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def auto_lookup_key(media_hash: str, audio_stream_index: int, stream_codec: str | None,
                    asr_model_name: str, asr_params: dict,
                    alignment_model: ModelInfo | None, pipeline_version: str) -> str:
    """Pre-ASR cache lookup key for AUTO source-language mode.

    cache_key() above requires the RESOLVED language, which for AUTO mode
    is only known AFTER ASR runs -- there is no way to compute that key
    before paying for the expensive step it exists to let you skip. This
    key deliberately excludes language instead of guessing it.

    That's safe, not just convenient: for AUTO mode, `AsrConfig.language`
    is always None -- a fixed input regardless of what gets detected. For
    MANUAL mode the requested language is NOT a fixed input -- it's fed
    to the decoder as a forced assumption (see asr.py), which can change
    the actual transcription -- so MANUAL mode keeps using cache_key()
    above as-is, with its already-known language, rather than this
    function. A given (media, stream, ASR config, pipeline version)
    combination has exactly one correct detected language, so a
    language-less lookup key for AUTO mode is not a weaker guarantee than
    cache_key()'s, it is simply keyed on what's actually known at lookup
    time.

    `asr_model_name`/`asr_params` are the STATIC config (AsrConfig.model_name
    and the same parameter dict asr._model_info() would build), not the
    ModelInfo produced after a model loads -- that also isn't available
    before ASR runs, for the same reason the language isn't. In the
    overwhelmingly common case the post-load model version equals
    `asr_model_name` unchanged; validate_cached_transcript() below is the
    safety net for the rare case it doesn't, or for any other drift --
    never trust this key path alone to mean the content is still right."""
    payload = json.dumps({
        "media_hash": media_hash, "audio_stream_index": audio_stream_index,
        "stream_codec": stream_codec, "asr_model_name": asr_model_name,
        "asr_params": asr_params,
        "alignment_model": asdict(alignment_model) if alignment_model else None,
        "pipeline_version": pipeline_version,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_cached_transcript(transcript: "CanonicalTranscript", *, media_hash: str,
                               audio_stream_index: int, pipeline_version: str,
                               asr_model_name: str) -> bool:
    """Defense in depth for a cache HIT, for both auto_lookup_key() and
    cache_key() callers. The lookup key already encodes these dimensions
    -- a mismatch here would mean a hash collision, a hand-copied file, or
    a future code change to what the key covers -- but re-checking the
    loaded content's OWN recorded fields costs nothing and turns any of
    those into a clean cache miss (safe: falls back to recomputing)
    instead of a silently wrong transcript being trusted on faith in a
    filename.

    Language is deliberately not part of this check: for an auto_lookup_key
    hit, the loaded transcript's `language` field IS the answer being
    retrieved, not a pre-ASR expectation to validate against. A MANUAL-mode
    caller using cache_key() already has language baked into the key
    itself, so a mismatch there manifests as a lookup MISS (different key),
    never as a hit needing this check."""
    return (transcript.media_hash == media_hash
           and transcript.audio_stream_index == audio_stream_index
           and transcript.pipeline_version == pipeline_version
           and transcript.asr_model.name == f"faster-whisper-{asr_model_name}")
