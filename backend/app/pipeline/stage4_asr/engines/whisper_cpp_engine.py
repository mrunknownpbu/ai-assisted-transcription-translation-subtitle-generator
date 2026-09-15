"""Fallback #2: whisper.cpp via subprocess. Runs a pure-C++ Whisper build with a much
smaller memory footprint than either Python engine — the intended fallback for small
CPU-only boxes where loading a PyTorch/CTranslate2 runtime at all is undesirable. Not
bundled by default: `is_available()` only reports true when a built binary + GGML model
file are actually present at the configured paths, since we can't ship a compiled binary
for every host architecture.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.pipeline.interfaces import ASRResult, ASRSegment, ASRWord
from app.pipeline.stage4_asr.engines.base import ASREngineError

logger = logging.getLogger("subtitle_platform.pipeline.asr.whisper_cpp")


class WhisperCppEngine:
    name = "whisper_cpp"

    def __init__(self, binary_path: str = "whisper-cli", model_path: str | None = None):
        self.binary_path = binary_path
        self.model_path = model_path

    @property
    def model_version(self) -> str:
        return f"whisper.cpp:{Path(self.model_path).name if self.model_path else 'unset'}"

    def is_available(self) -> tuple[bool, str | None]:
        if shutil.which(self.binary_path) is None:
            return False, f"whisper.cpp binary '{self.binary_path}' not found on PATH"
        if not self.model_path or not Path(self.model_path).is_file():
            return False, f"whisper.cpp GGML model file not found at '{self.model_path}'"
        return True, None

    def transcribe(self, audio_path: Path, language: str | None) -> ASRResult:
        with tempfile.TemporaryDirectory() as tmp:
            out_prefix = Path(tmp) / "out"
            cmd = [
                self.binary_path, "-m", self.model_path, "-f", str(audio_path),
                "-oj", "-of", str(out_prefix), "-ml", "1",  # -ml 1: word-level segment splitting
            ]
            if language:
                cmd += ["-l", language]
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=1800, check=True)
                data = json.loads((out_prefix.with_suffix(".json")).read_text())
            except Exception as exc:
                raise ASREngineError(f"whisper.cpp transcription failed: {exc}") from exc

            segments: list[ASRSegment] = []
            for i, seg in enumerate(data.get("transcription", [])):
                text = seg.get("text", "").strip()
                start = _ts_to_seconds(seg["offsets"]["from"])
                end = _ts_to_seconds(seg["offsets"]["to"])
                # whisper.cpp's default JSON has no per-word confidence; treat the whole
                # segment as one "word" span rather than fabricating a confidence value.
                words = [ASRWord(word=text, start=start, end=end, confidence=1.0)] if text else []
                segments.append(ASRSegment(
                    segment_id=f"seg_{i:05d}", start=start, end=end, text=text,
                    words=words, avg_confidence=1.0 if text else 0.0,
                ))

            return ASRResult(
                engine=self.name, model_version=self.model_version,
                language=language or data.get("result", {}).get("language", "und"),
                segments=segments,
            )


def _ts_to_seconds(ms: int) -> float:
    return ms / 1000.0
