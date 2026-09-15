"""Fallback #1: the reference `openai-whisper` package. Slower and heavier than
faster-whisper but has fewer moving parts (no CTranslate2 conversion step), so it's kept
as the first fallback for environments where faster-whisper fails to load."""
from __future__ import annotations

import logging
from pathlib import Path

from app.pipeline.interfaces import ASRResult, ASRSegment, ASRWord
from app.pipeline.stage4_asr.engines.base import ASREngineError

logger = logging.getLogger("subtitle_platform.pipeline.asr.openai_whisper")


class OpenAIWhisperEngine:
    name = "openai_whisper"

    def __init__(self, model_size: str = "medium", device: str | None = None, download_root: str | None = None):
        self.model_size = model_size
        self.device = device
        self.download_root = download_root
        self._model = None

    @property
    def model_version(self) -> str:
        return f"openai-whisper:{self.model_size}"

    def is_available(self) -> tuple[bool, str | None]:
        try:
            import whisper  # noqa: F401
        except ImportError as exc:
            return False, f"openai-whisper package not importable: {exc}"
        return True, None

    def unload(self) -> None:
        """PyTorch-backed model — dropping the reference lets `core/gpu.py::
        unload_engines`'s subsequent `torch.cuda.empty_cache()` actually reclaim its VRAM."""
        self._model = None

    def _load(self):
        if self._model is None:
            import whisper
            self._model = whisper.load_model(self.model_size, device=self.device, download_root=self.download_root)
        return self._model

    def transcribe(self, audio_path: Path, language: str | None) -> ASRResult:
        try:
            model = self._load()
            result = model.transcribe(
                str(audio_path), language=language, task="transcribe",
                word_timestamps=True, condition_on_previous_text=False,
            )

            segments: list[ASRSegment] = []
            for i, seg in enumerate(result.get("segments", [])):
                words = [
                    ASRWord(word=w["word"].strip(), start=w["start"], end=w["end"],
                            confidence=float(w.get("probability", 0.0)))
                    for w in seg.get("words", [])
                ]
                avg_conf = (sum(w.confidence for w in words) / len(words)) if words else 0.0
                segments.append(ASRSegment(
                    segment_id=f"seg_{i:05d}", start=seg["start"], end=seg["end"], text=seg["text"].strip(),
                    words=words, avg_confidence=avg_conf, no_speech_prob=float(seg.get("no_speech_prob", 0.0)),
                ))

            return ASRResult(
                engine=self.name, model_version=self.model_version,
                language=language or result.get("language", "und"), segments=segments,
            )
        except Exception as exc:
            raise ASREngineError(f"openai_whisper transcription failed: {exc}") from exc
