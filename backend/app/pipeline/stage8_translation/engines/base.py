from __future__ import annotations

from typing import Protocol


class TranslationEngine(Protocol):
    name: str

    def is_available(self) -> tuple[bool, str | None]: ...

    def translate(self, text: str, source_flores: str, target_flores: str) -> str:
        """Both language args are already-resolved FLORES-200 tags — resolution happens
        once in the orchestrator, not repeatedly inside each engine."""
        ...


class TranslationEngineError(RuntimeError):
    pass
