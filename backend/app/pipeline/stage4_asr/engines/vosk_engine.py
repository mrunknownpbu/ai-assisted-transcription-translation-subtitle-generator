"""Last-resort fallback: Vosk. A different model family entirely from Whisper, kept at
the end of the chain purely so a job never hard-fails just because every Whisper variant
(local x3 + API) is unavailable — quality is lower than Whisper, and this is documented
in the transcript's provenance record so downstream consumers know a degraded engine
produced it. Requires a pre-downloaded Vosk model directory (not bundled)."""
from __future__ import annotations

import json
import logging
import wave
from pathlib import Path

from app.core.audio import extract_wav_file
from app.pipeline.interfaces import ASRResult, ASRSegment, ASRWord
from app.pipeline.stage4_asr.engines.base import ASREngineError

logger = logging.getLogger("subtitle_platform.pipeline.asr.vosk")


class VoskEngine:
    name = "vosk"

    def __init__(self, model_path: str | None, work_dir: str | Path = "/tmp"):
        self.model_path = model_path
        self.work_dir = Path(work_dir)
        self._model = None

    @property
    def model_version(self) -> str:
        return f"vosk:{Path(self.model_path).name if self.model_path else 'unset'}"

    def is_available(self) -> tuple[bool, str | None]:
        if not self.model_path or not Path(self.model_path).is_dir():
            return False, f"Vosk model directory not found at '{self.model_path}'"
        try:
            import vosk  # noqa: F401
        except ImportError as exc:
            return False, f"vosk package not importable: {exc}"
        return True, None

    def _load(self):
        if self._model is None:
            import vosk
            vosk.SetLogLevel(-1)
            self._model = vosk.Model(self.model_path)
        return self._model

    def transcribe(self, audio_path: Path, language: str | None) -> ASRResult:
        import vosk

        try:
            wav_path = extract_wav_file(audio_path, 0, self.work_dir / f"vosk_input_{audio_path.stem}.wav",
                                         sample_rate=16000)
            model = self._load()
            with wave.open(str(wav_path), "rb") as wf:
                rec = vosk.KaldiRecognizer(model, wf.getframerate())
                rec.SetWords(True)

                all_words: list[dict] = []
                full_text_parts: list[str] = []
                while True:
                    data = wf.readframes(4000)
                    if not data:
                        break
                    if rec.AcceptWaveform(data):
                        chunk = json.loads(rec.Result())
                        all_words.extend(chunk.get("result", []))
                        if chunk.get("text"):
                            full_text_parts.append(chunk["text"])
                final = json.loads(rec.FinalResult())
                all_words.extend(final.get("result", []))
                if final.get("text"):
                    full_text_parts.append(final["text"])

            words = [ASRWord(word=w["word"], start=w["start"], end=w["end"], confidence=float(w.get("conf", 0.5)))
                     for w in all_words]
            avg_conf = (sum(w.confidence for w in words) / len(words)) if words else 0.0
            text = " ".join(full_text_parts).strip()

            segments = [ASRSegment(
                segment_id="seg_00000",
                start=words[0].start if words else 0.0,
                end=words[-1].end if words else 0.0,
                text=text, words=words, avg_confidence=avg_conf,
            )] if words else []

            return ASRResult(engine=self.name, model_version=self.model_version,
                              language=language or "und", segments=segments)
        except Exception as exc:
            raise ASREngineError(f"vosk transcription failed: {exc}") from exc
