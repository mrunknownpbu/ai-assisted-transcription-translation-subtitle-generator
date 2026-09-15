"""Primary ASR engine: faster-whisper (CTranslate2 reimplementation of the OpenAI Whisper
model weights). Chosen as primary over the reference `openai-whisper` package because it
is materially faster and lower-memory on both CPU and GPU while using the same trained
weights — the reference implementation is kept one step down in the fallback chain for
environments where CTranslate2 can't be used.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.hardware import HardwareProfile, recommended_whisper_model
from app.pipeline.interfaces import ASRResult, ASRSegment, ASRWord
from app.pipeline.stage4_asr.engines.base import ASREngineError

logger = logging.getLogger("subtitle_platform.pipeline.asr.faster_whisper")


class FasterWhisperEngine:
    name = "faster_whisper"

    def __init__(self, model_size: str = "medium", device: str = "auto", compute_type: str = "auto",
                 hardware_profile: HardwareProfile | None = None, download_root: str | None = None,
                 hotwords: str | None = None):
        self.requested_model_size = model_size
        self.model_size = (
            recommended_whisper_model(hardware_profile, model_size) if hardware_profile else model_size
        )
        self.device = device
        self.compute_type = compute_type
        self.download_root = download_root
        # Biases decoding toward specific proper nouns throughout the *entire* audio (not
        # just an initial prompt window -- confirmed by reading faster-whisper's own
        # transcribe loop: hotwords is threaded into get_prompt() on every segment, unlike
        # initial_prompt/prefix which only ever affect the first). Built from this job's
        # already-resolved glossary (TVDB cast + local profile + explicit overrides) by
        # worker.py -- see its _build_asr_hotwords(). Real motivation: a translation-
        # accuracy sweep across 5 real episodes of a Turkish drama found the ASR itself
        # (running on the VRAM-forced "medium" model, not "large-v3") consistently
        # misheard main-cast names it had never been told to expect -- "Serkan" -> "Selkan"
        # (13x), "Bolat" -> "Balat" (8x), "Aydan" -> "Aydın" (10x), "Selin" -> "Senin" (6x)
        # -- errors that are invisible to translation-time glossary protection, since
        # protection can only restore a name it recognizes in the source text, and by then
        # the ASR has already written the wrong word.
        self.hotwords = hotwords
        self._model = None

    @property
    def model_version(self) -> str:
        return f"faster-whisper:{self.model_size}"

    def is_available(self) -> tuple[bool, str | None]:
        try:
            import faster_whisper  # noqa: F401
        except ImportError as exc:
            return False, f"faster_whisper package not importable: {exc}"
        return True, None

    def unload(self) -> None:
        """Drops the CTranslate2-backed model so its GPU memory pool is released on
        destruction — called by `core/gpu.py::unload_engines` between the ASR stage and
        translation, before this job's GPU slot is released back to the semaphore."""
        self._model = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type,
                download_root=self.download_root,
            )
        return self._model

    def transcribe(self, audio_path: Path, language: str | None) -> ASRResult:
        try:
            model = self._load()
            segments_iter, info = model.transcribe(
                str(audio_path), language=language, task="transcribe",
                word_timestamps=True, condition_on_previous_text=False, vad_filter=False,
                # vad_filter is intentionally off here: Stage 6 owns hallucination/VAD
                # policy explicitly (with logged, tunable thresholds) rather than having
                # it silently applied and undocumented inside the ASR engine.
                hotwords=self.hotwords or None,
            )

            segments: list[ASRSegment] = []
            for i, seg in enumerate(segments_iter):
                # faster-whisper's word timings can come back as numpy scalars from its
                # internal DTW alignment; cast to plain Python floats immediately at this
                # boundary so nothing downstream (DB JSON columns included) ever has to
                # deal with a numpy type leaking through arithmetic or comparisons.
                words = [
                    ASRWord(word=w.word.strip(), start=float(w.start), end=float(w.end),
                            confidence=float(w.probability))
                    for w in (seg.words or [])
                ]
                avg_conf = (sum(w.confidence for w in words) / len(words)) if words else 0.0
                segments.append(ASRSegment(
                    segment_id=f"seg_{i:05d}", start=float(seg.start), end=float(seg.end), text=seg.text.strip(),
                    words=words, avg_confidence=avg_conf, no_speech_prob=float(seg.no_speech_prob),
                    # Both exposed directly by faster-whisper's Segment; feed Stage 6's
                    # weighted hallucination scoring (compression_ratio>=2.4 and
                    # avg_logprob<=-1.0 are two of its acoustic signals).
                    compression_ratio=float(seg.compression_ratio), avg_logprob=float(seg.avg_logprob),
                ))

            return ASRResult(
                engine=self.name, model_version=self.model_version,
                language=language or info.language, segments=segments,
            )
        except Exception as exc:
            raise ASREngineError(f"faster_whisper transcription failed: {exc}") from exc
