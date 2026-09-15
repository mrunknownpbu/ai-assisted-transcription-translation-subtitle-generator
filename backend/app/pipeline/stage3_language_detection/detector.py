"""Stage 3: Language Detection.

Detects the spoken language directly from the selected audio stream's waveform — never
from filename, container language tags, or embedded subtitle language tags (those are
recorded upstream as evidence only). Default mode is auto-detect; manual override is
applied via `apply_manual_override` and is tracked separately from the detector's own
result so the original detection + confidence is never lost, even when overridden.

Uses Whisper's own encoder-based language-ID head (via faster-whisper) as the detection
method: it's the same acoustic model family used for transcription, avoiding the need for
a second, separately-maintained language-ID model.

**Multi-window sampling, not just the first 30 seconds.** A single window at the very
start of a long file is a real, confirmed failure mode: a cold open, a "previously on"
recap, a title sequence, or distributor branding is very often mixed or dubbed
differently from the body of the episode, and can misdetect the language entirely — this
was directly observed in production (a 131-minute single-audio-track episode returned
'en' at 35% confidence from a first-30-seconds sample of what was actually a non-English
drama). When the stream's duration is known, `detect_language` instead samples several
windows spread across the middle of the runtime (skipping the first/last 10%, where cold
opens and credits live) and aggregates by average confidence per language, so one
unrepresentative window can't dominate the result the way relying on it alone did.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from app.core.audio import AudioDecodeError, decode_pcm_array
from app.pipeline.interfaces import LanguageDetectionResult

logger = logging.getLogger("subtitle_platform.pipeline.language_detection")

LANGUAGE_ID_SAMPLE_SECONDS = 30  # matches Whisper's native context window
DEFAULT_NUM_WINDOWS = 4


class LanguageIdEngine(Protocol):
    method_name: str

    def detect(self, pcm_16k_mono, sample_rate: int) -> tuple[str, float, list[tuple[str, float]]]: ...


class FasterWhisperLanguageId:
    """Lazy-loads a small faster-whisper model purely for its language-ID head. Kept
    separate from the Stage 4 ASR engine instance (which may load a larger model) since
    language ID only needs a small/fast model and should not force a large model load
    before the user has even confirmed the language."""

    method_name = "faster_whisper_lid"

    def __init__(self, model_size: str = "tiny", device: str = "auto", compute_type: str = "auto"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        return self._model

    def detect(self, pcm_16k_mono, sample_rate: int) -> tuple[str, float, list[tuple[str, float]]]:
        model = self._load()
        _, info = model.transcribe(pcm_16k_mono, language=None, task="transcribe", without_timestamps=True,
                                    condition_on_previous_text=False)
        # faster-whisper exposes all_language_probs when language=None
        probs = getattr(info, "all_language_probs", None) or []
        candidates = sorted(((lang, prob) for lang, prob in probs), key=lambda x: x[1], reverse=True)[:5]
        return info.language, float(info.language_probability), candidates


def _window_offsets(duration_s: float, num_windows: int, window_seconds: float) -> list[float]:
    """Spreads sample windows across the middle 80% of the runtime (skipping the first/
    last 10%, where cold opens, recaps, title sequences, and credits live) rather than
    only ever sampling from the very start."""
    if duration_s <= window_seconds:
        return [0.0]

    span_start = duration_s * 0.10
    span_end = max(span_start, duration_s * 0.90 - window_seconds)
    if span_end <= span_start:
        return [span_start]
    if num_windows == 1:
        return [span_start]

    step = (span_end - span_start) / (num_windows - 1)
    return [span_start + i * step for i in range(num_windows)]


def detect_language(
    source_path: str | Path, stream_index: int, engine: LanguageIdEngine | None = None,
    duration_s: float | None = None, num_windows: int = DEFAULT_NUM_WINDOWS,
) -> LanguageDetectionResult:
    engine = engine or FasterWhisperLanguageId()
    offsets = _window_offsets(duration_s, num_windows, LANGUAGE_ID_SAMPLE_SECONDS) if duration_s else [0.0]

    window_results: list[tuple[str, float, list[tuple[str, float]]]] = []
    for offset in offsets:
        try:
            pcm = decode_pcm_array(source_path, stream_index, seconds=LANGUAGE_ID_SAMPLE_SECONDS, start_seconds=offset)
        except AudioDecodeError as exc:
            logger.warning("Language-ID window at %.1fs failed to decode, skipping: %s", offset, exc)
            continue
        if pcm.size == 0:
            continue
        window_results.append(engine.detect(pcm, 16000))

    if not window_results:
        logger.warning("Stream %d produced no decodable audio for language ID", stream_index)
        return LanguageDetectionResult(detected_language="und", confidence=0.0, method=engine.method_name)

    if len(window_results) == 1:
        language, confidence, candidates = window_results[0]
        return LanguageDetectionResult(detected_language=language, confidence=confidence,
                                        method=engine.method_name, candidates=candidates)

    # Aggregate by average confidence per language across windows — the language with the
    # highest total wins, so one unrepresentative window (a differently-dubbed cold open,
    # for instance) can't dominate the way trusting a single window does.
    scores: dict[str, list[float]] = {}
    for language, confidence, _ in window_results:
        scores.setdefault(language, []).append(confidence)

    best_language = max(scores, key=lambda lang: sum(scores[lang]))
    aggregate_confidence = sum(scores[best_language]) / len(window_results)
    agreement = len(scores[best_language]) / len(window_results)

    all_candidates = sorted(
        ((lang, sum(confs) / len(window_results)) for lang, confs in scores.items()),
        key=lambda pair: pair[1], reverse=True,
    )[:5]

    method = f"{engine.method_name}_multi_window(n={len(window_results)},agreement={agreement:.2f})"
    logger.info("Multi-window language ID: %d windows, winner=%s (confidence=%.2f, agreement=%.0f%%)",
                len(window_results), best_language, aggregate_confidence, agreement * 100)

    return LanguageDetectionResult(
        detected_language=best_language, confidence=aggregate_confidence, method=method, candidates=all_candidates,
    )


def apply_manual_override(result: LanguageDetectionResult, language: str) -> LanguageDetectionResult:
    return LanguageDetectionResult(
        detected_language=result.detected_language,
        confidence=result.confidence,
        method=result.method,
        candidates=result.candidates,
        overridden=True,
        effective_language=language,
    )
