"""Audio-stream discovery, candidate analysis, and ranking.

A media file may carry several audio tracks (dubs, commentary, audio
description, duplicate encodes). Stream *selection* is a first-class
pipeline stage here, not an implicit "stream 0" / "container default"
assumption -- see pipeline.py's use of recommend_stream() for AUTO mode.

Two-tier language signal, deliberately kept separate everywhere:
  - AudioStream.language: the container's own tag -- evidence, never
    truth (a file can be mislabeled).
  - StreamCandidate.detected_language: THIS module's own short-sample
    language ID, used only to rank/choose a stream cheaply. The
    pipeline's full transcription of the chosen stream produces its own,
    authoritative detected language/confidence for the job record (see
    pipeline.py) -- never conflate the two.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import media

# Disposition/keyword signals that mark a track as something other than
# the primary dialogue mix. Excluded from AUTO ranking/recommendation,
# but never hidden from the GUI or from manual override -- see
# AudioStream.exclusion_reason and recommend_stream().
_EXCLUSION_KEYWORDS = ("commentary", "director", "description", "descriptive",
                       "narration", "narrator", "sdh")

SAMPLE_OFFSET_SECONDS = 180.0     # skip cold-open/title cards when the file is long enough
SAMPLE_DURATION_SECONDS = 20.0
CLOSE_SCORE_MARGIN = 0.15         # candidates within this margin of the top score are "alternates"

# ISO 639-2/B (what MKV/ffprobe "language" tags normally use) -> ISO 639-1
# (what faster-whisper's language detector returns). A plain prefix check
# is WRONG for exactly the languages this project cares about most --
# "tur" does not start with "tr", "jpn" does not start with "ja" -- so an
# explicit table is the only correct way to compare the two.
_ISO_639_2_TO_1 = {
    "tur": "tr", "eng": "en", "jpn": "ja", "kor": "ko", "spa": "es", "fra": "fr", "fre": "fr",
    "deu": "de", "ger": "de", "rus": "ru", "ara": "ar", "ita": "it", "por": "pt", "chi": "zh",
    "zho": "zh", "nld": "nl", "dut": "nl", "pol": "pl", "swe": "sv", "vie": "vi", "tha": "th",
    "hin": "hi", "ell": "el", "gre": "el", "heb": "he", "ces": "cs", "cze": "cs", "dan": "da",
    "fin": "fi", "nor": "no", "ron": "ro", "rum": "ro", "ukr": "uk", "ind": "id", "msa": "ms",
    "may": "ms", "tam": "ta", "tel": "te",
    # Found missing by a real multi-language validation run (a genuine
    # Tamil/Telugu/Hindi/Malayalam/Kannada multi-audio release): "mal" and
    # "kan" fell through to the [:2] fallback ("ma"/"ka"), producing a
    # false "embedded language does NOT match detected speech" report even
    # when a stream's tag and its actual audio agreed. "tam"/"tel" above
    # happen to survive the fallback correctly and don't strictly need an
    # entry, but are listed explicitly now that this language family is a
    # real, tested case, not a coincidence to rely on silently.
    "mal": "ml", "kan": "kn", "ben": "bn", "mar": "mr",
}


def normalize_lang(code: str | None) -> str | None:
    """Best-effort ISO 639-1 normalization for COMPARISON purposes only
    -- never used to decide the actual transcription language, which
    always comes from real audio evidence (see pipeline.py). An unknown
    3-letter code falls back to its first two letters rather than raising
    -- a wrong guess here only costs a ranking bonus, never correctness."""
    if not code:
        return None
    code = code.lower()
    return code if len(code) == 2 else _ISO_639_2_TO_1.get(code, code[:2])


@dataclass
class AudioStream:
    index: int
    codec: str | None = None
    codec_long_name: str | None = None
    channels: int | None = None
    channel_layout: str | None = None
    sample_rate: int | None = None
    bit_rate: int | None = None
    duration: float | None = None
    title: str | None = None
    language: str | None = None       # embedded/tagged, raw ffprobe value (e.g. "tur")
    handler_name: str | None = None
    default: bool = False
    forced: bool = False
    hearing_impaired: bool = False
    visual_impaired: bool = False
    commentary: bool = False

    @property
    def exclusion_reason(self) -> str | None:
        """Why this stream should never be AUTO-recommended as the
        primary dialogue track. It remains fully visible and selectable
        via manual override -- exclusion only affects ranking."""
        if self.commentary:
            return "commentary disposition"
        if self.visual_impaired:
            return "audio-description / visual-impaired disposition"
        if self.hearing_impaired:
            return "hearing-impaired disposition"
        haystack = f"{self.title or ''} {self.handler_name or ''}".lower()
        for kw in _EXCLUSION_KEYWORDS:
            if kw in haystack:
                return f"title/handler mentions {kw!r}"
        return None


@dataclass
class StreamCandidate:
    stream: AudioStream
    detected_language: str | None = None
    detection_confidence: float | None = None
    score: float = 0.0
    reason: str = ""


@dataclass
class StreamRecommendation:
    streams: list[AudioStream]                       # every audio stream -- for GUI display
    ranked: list[StreamCandidate]                     # scored, best first (excluded streams omitted)
    recommended_index: int
    recommended_language: str | None
    recommended_confidence: float | None
    reason: str
    alternates: list[StreamCandidate] = field(default_factory=list)


def _disposition(stream: dict, key: str) -> bool:
    return bool(stream.get("disposition", {}).get(key))


def parse_audio_streams(streams: list[dict]) -> list[AudioStream]:
    """Typed view over media.probe()'s raw ffprobe stream dicts -- the
    ffprobe invocation already requests every field ffprobe knows about
    each stream; this just gives it a stable, explicit shape instead of
    ad hoc dict indexing spread across callers."""
    out = []
    for s in streams:
        if s.get("codec_type") != "audio":
            continue
        tags = s.get("tags", {}) or {}
        bit_rate = s.get("bit_rate")
        sample_rate = s.get("sample_rate")
        duration = s.get("duration")
        out.append(AudioStream(
            index=s["index"], codec=s.get("codec_name"), codec_long_name=s.get("codec_long_name"),
            channels=s.get("channels"), channel_layout=s.get("channel_layout"),
            sample_rate=int(sample_rate) if sample_rate else None,
            bit_rate=int(bit_rate) if bit_rate else None,
            duration=float(duration) if duration else None,
            title=tags.get("title"), language=tags.get("language"),
            handler_name=tags.get("handler_name"),
            default=_disposition(s, "default"), forced=_disposition(s, "forced"),
            hearing_impaired=_disposition(s, "hearing_impaired"),
            visual_impaired=_disposition(s, "visual_impaired"),
            commentary=_disposition(s, "comment"),
        ))
    return out


def extract_sample(video_path: str | Path, stream_index: int, work_dir: Path, *,
                   total_duration: float | None = None,
                   offset: float = SAMPLE_OFFSET_SECONDS,
                   duration: float = SAMPLE_DURATION_SECONDS) -> Path:
    """A short, representative clip of ONE stream -- never the whole
    file -- cheap enough to run once per candidate stream. Clamped to fit
    inside a short, or already-mostly-elapsed, file."""
    if total_duration and offset + duration > total_duration:
        offset = max(0.0, total_duration - duration)
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / f"sample_{stream_index}.wav"
    command = ["ffmpeg", "-y", "-ss", str(offset), "-t", str(duration), "-i", str(video_path),
              "-map", f"0:{stream_index}", "-ac", "1", "-ar", "16000", "-vn", "-sn", str(out)]
    try:
        subprocess.run(command, capture_output=True, text=True, timeout=60, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise media.MediaError(f"ffmpeg sample extraction failed: {exc}") from exc
    return out


def default_sampler(model_name: str = "large-v3"):
    """Lazily loads a faster-whisper model once, returning a
    (wav_path) -> (language, probability) callable.

    Defaults to large-v3 -- the same model used for the real
    transcription -- because that is the only faster-whisper model this
    deployment's read-only /models actually has cached; faster-whisper
    cannot download a smaller one (e.g. "base") into it at runtime. A
    deployment that pre-downloads a smaller model into /models during the
    image build MAY pass model_name="base"/"small" here for less
    transient VRAM pressure while a full job might already be using the
    GPU -- but that is an opt-in, not a default this code can assume."""
    state: dict = {}

    def sample(wav_path: Path) -> tuple[str, float]:
        if "model" not in state:
            from faster_whisper import WhisperModel
            # compute_type sourced from asr.AsrConfig's own default, not a
            # second hardcoded "float16" -- real defect (2026-09-17): this
            # function used to hardcode float16 independently of asr.py's
            # AsrConfig, so fixing the ASR stage's float16/Tesla-P4
            # mismatch here still left THIS model construction broken,
            # confirmed by a real job's AUDIO_STREAM_RECOMMENDED falling
            # back to "language sampling failed" and a weaker default
            # disposition instead of real per-stream language detection.
            from asr import AsrConfig
            state["model"] = WhisperModel(model_name, device="cuda",
                                          compute_type=AsrConfig().compute_type,
                                          download_root="/models")
        model = state["model"]
        _segments, info = model.transcribe(str(wav_path), beam_size=1, vad_filter=True)
        return info.language, float(info.language_probability)

    return sample


def score_candidate(stream: AudioStream, detected_language: str | None,
                    detection_confidence: float | None, *,
                    total_duration: float | None, preferred_language: str | None) -> tuple[float, str]:
    """Deterministic, explainable ranking -- every component that fires
    is named in the reason string, so a recommendation is never a black
    box. Weights are additive and each capped in [0, ~0.2] so no single
    signal (e.g. "default disposition") can dominate a strong detection
    signal pointing the other way."""
    score = 0.0
    reasons: list[str] = []
    if detection_confidence is not None:
        score += 0.45 * detection_confidence
        reasons.append(f"detected {detected_language} at {detection_confidence:.0%} confidence")
    if stream.default:
        score += 0.20
        reasons.append("default disposition")
    embedded_norm = normalize_lang(stream.language)
    if embedded_norm and detected_language:
        if embedded_norm == detected_language:
            score += 0.15
            reasons.append(f"embedded language {stream.language!r} matches detected speech")
        else:
            reasons.append(f"embedded language {stream.language!r} does NOT match detected "
                           f"speech ({detected_language}) -- metadata is evidence, not truth")
    if preferred_language and detected_language:
        if normalize_lang(preferred_language) == detected_language:
            reasons.append(f"matches requested source language {preferred_language!r}")
        else:
            reasons.append(f"does not match requested source language {preferred_language!r}")
    if stream.channels and stream.channels >= 2:
        score += 0.05
        reasons.append("stereo or better")
    if total_duration and stream.duration and stream.duration >= 0.9 * total_duration:
        score += 0.05
        reasons.append("spans the full runtime")
    return score, ("; ".join(reasons) if reasons else "no distinguishing signal")


def recommend_stream(video_path: str | Path, work_dir: Path, *, preferred_language: str | None = None,
                     sampler=None, total_duration: float | None = None,
                     streams: list[AudioStream] | None = None) -> StreamRecommendation:
    """Enumerate -> exclude obvious non-dialogue tracks -> short-sample
    language ID on the remaining candidates only -> score -> rank. Full
    ASR never runs here -- see module docstring."""
    if streams is None:
        raw_streams, fmt = media.probe(video_path)
        streams = parse_audio_streams(raw_streams)
        if total_duration is None:
            total_duration = float(fmt.get("duration") or 0) or None
    if not streams:
        raise media.MediaError("no audio stream found")

    candidates = [s for s in streams if s.exclusion_reason is None] or list(streams)
    owns_sampler = sampler is None
    sampler = sampler or default_sampler()

    # Sampling loads a full faster-whisper model (see default_sampler's
    # docstring: this deployment's read-only /models only has large-v3
    # cached, so the sampler is exactly as GPU-heavy as the real ASR
    # pass) -- held under the same cross-process lock as the real ASR/
    # translation stages so a concurrent job's model load can't collide
    # with it (confirmed by a real CUDA OOM under 2-job concurrency).
    from gpu import gpu_lock
    with gpu_lock():
        scored: list[StreamCandidate] = []
        for stream in candidates:
            sample_error = None
            try:
                wav = extract_sample(video_path, stream.index, work_dir, total_duration=total_duration)
                language, confidence = sampler(wav)
            except Exception as exc:
                # One bad candidate (corrupt stream, transient ffmpeg failure)
                # must not abort the whole recommendation -- but silently
                # falling back to (None, None) here once made a real sampler
                # misconfiguration (wrong model name for this deployment's
                # read-only /models) indistinguishable from "no speech
                # detected"; the reason string now always says which happened.
                language, confidence, sample_error = None, None, exc
                # A failed sample (e.g. a CUDA OOM constructing the
                # sampler's model) can leave partially-allocated GPU
                # memory that nothing Python-visible references -- clean
                # up immediately rather than letting every remaining
                # candidate retry the same failing allocation on top of it.
                from gpu import free_gpu
                free_gpu()
            score, reason = score_candidate(stream, language, confidence,
                                            total_duration=total_duration, preferred_language=preferred_language)
            if sample_error is not None:
                reason = f"language sampling failed ({sample_error}); {reason}"
            scored.append(StreamCandidate(stream=stream, detected_language=language,
                                          detection_confidence=confidence, score=score, reason=reason))

        if owns_sampler:
            # We created this sampler, so we're the only owner of the
            # faster-whisper model it lazily loaded into its closure --
            # free it deterministically instead of leaving it to whenever
            # the garbage collector gets to it (see gpu.py: VRAM isn't
            # released to other processes until the model is actually
            # collected and empty_cache() runs).
            del sampler
            from gpu import free_gpu
            free_gpu()

    # A candidate whose detected speech matches an explicitly requested
    # source language always outranks one that doesn't, regardless of raw
    # score -- deliberate user intent beats incidental signals like
    # container metadata or default disposition. Score only breaks ties
    # within each group. A soft point-bonus was tried and rejected: a
    # strong "default + embedded tag matches" prior could still outscore
    # it, silently ignoring the user's explicit request.
    preferred_norm = normalize_lang(preferred_language) if preferred_language else None
    if preferred_norm:
        scored.sort(key=lambda c: (c.detected_language != preferred_norm, -c.score))
    else:
        scored.sort(key=lambda c: -c.score)
    best = scored[0]
    alternates = [c for c in scored[1:] if best.score - c.score <= CLOSE_SCORE_MARGIN]
    reason = best.reason
    if alternates:
        described = [f"#{a.stream.index} {a.detected_language} {a.detection_confidence:.0%}"
                    for a in alternates if a.detection_confidence is not None]
        if described:
            reason += "; close alternate(s): " + ", ".join(described)

    return StreamRecommendation(
        streams=streams, ranked=scored, recommended_index=best.stream.index,
        recommended_language=best.detected_language, recommended_confidence=best.detection_confidence,
        reason=reason, alternates=alternates,
    )
