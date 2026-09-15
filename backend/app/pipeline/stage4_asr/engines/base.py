"""Common ASR engine contract. Every engine — Whisper-family or not — normalizes its
output into the same ASRResult/ASRSegment/ASRWord shape from `pipeline.interfaces`, so
Stage 5 (canonical transcript) never needs to know which engine actually ran.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.pipeline.interfaces import ASRResult


class ASREngine(Protocol):
    name: str

    def is_available(self) -> tuple[bool, str | None]:
        """Returns (available, reason_if_not). Must never raise — availability checks
        are cheap/local (binary present, model dir exists, API key configured), not a
        full model load, so the fallback chain can probe every engine quickly."""
        ...

    def transcribe(self, audio_path: Path, language: str | None) -> ASRResult:
        """`language=None` means auto-detect (engine's own language ID, if it has one).
        Must raise on failure rather than return a partial/empty result, so the fallback
        chain can distinguish 'this engine produced nothing usable' from 'silence'."""
        ...


class ASREngineError(RuntimeError):
    """Raised by an engine's transcribe() on failure; caught by the fallback chain."""
