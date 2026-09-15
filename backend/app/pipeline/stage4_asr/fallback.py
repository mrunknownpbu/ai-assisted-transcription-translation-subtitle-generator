"""Stage 4: ASR fallback orchestration.

Walks the configured engine chain in order. For each engine: check availability first
(cheap, local), skip with a logged reason if unavailable; if available, attempt
transcription and catch engine-specific failures so one broken engine can't take down
the whole job. The first engine to succeed wins. Every attempt (skipped or failed) is
recorded and returned as part of the provenance-bearing `TranscriptionOutcome`, and the
docs/API surface this chain, so a job's transcript always says which engine actually
produced it and what was tried first.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.pipeline.interfaces import ASRResult, EngineAttempt
from app.pipeline.stage4_asr.engines.base import ASREngine, ASREngineError

logger = logging.getLogger("subtitle_platform.pipeline.asr.fallback")


@dataclass(frozen=True)
class TranscriptionOutcome:
    result: ASRResult
    attempts: list[EngineAttempt]


class ASRUnavailableError(RuntimeError):
    def __init__(self, attempts: list[EngineAttempt]):
        self.attempts = attempts
        summary = "; ".join(f"{a.engine}: {a.detail}" for a in attempts)
        super().__init__(f"All configured ASR engines failed or were unavailable — {summary}")


def transcribe_with_fallback(
    audio_path: Path, language: str | None, engines: list[ASREngine],
) -> TranscriptionOutcome:
    if not engines:
        raise ValueError("No ASR engines configured — cannot transcribe")

    attempts: list[EngineAttempt] = []

    for engine in engines:
        available, reason = engine.is_available()
        if not available:
            logger.info("ASR engine '%s' unavailable, skipping: %s", engine.name, reason)
            attempts.append(EngineAttempt(engine=engine.name, outcome="skipped_unavailable", detail=reason))
            continue

        try:
            logger.info("Attempting transcription with ASR engine '%s'", engine.name)
            result = engine.transcribe(audio_path, language)
            attempts.append(EngineAttempt(engine=engine.name, outcome="succeeded"))
            return TranscriptionOutcome(result=result, attempts=attempts)
        except ASREngineError as exc:
            logger.warning("ASR engine '%s' failed: %s", engine.name, exc)
            attempts.append(EngineAttempt(engine=engine.name, outcome="failed", detail=str(exc)))
            continue

    raise ASRUnavailableError(attempts)


def build_default_engine_chain(settings, hardware_profile=None, hotwords: str | None = None) -> list[ASREngine]:
    """Instantiates the configured chain by name. Kept separate from
    `transcribe_with_fallback` so tests can pass in fakes/mocks directly instead of
    going through config-driven construction.

    `hotwords` (a short comma-separated hint string built from a job's resolved glossary
    -- see worker.py's `_build_asr_hotwords()`) is only wired into `FasterWhisperEngine`,
    the one engine in this chain that supports it; every other engine ignores it entirely
    rather than being forced to accept a parameter it can't use."""
    from app.pipeline.stage4_asr.engines.faster_whisper_engine import FasterWhisperEngine
    from app.pipeline.stage4_asr.engines.openai_whisper_engine import OpenAIWhisperEngine
    from app.pipeline.stage4_asr.engines.vosk_engine import VoskEngine
    from app.pipeline.stage4_asr.engines.whisper_api_engine import WhisperAPIEngine
    from app.pipeline.stage4_asr.engines.whisper_cpp_engine import WhisperCppEngine

    registry = {
        "faster_whisper": lambda: FasterWhisperEngine(
            model_size=settings.whisper_model_size, hardware_profile=hardware_profile,
            download_root=str(settings.models_dir / "whisper"), hotwords=hotwords,
        ),
        "openai_whisper": lambda: OpenAIWhisperEngine(
            model_size=settings.whisper_model_size, download_root=str(settings.models_dir / "whisper"),
        ),
        "whisper_cpp": lambda: WhisperCppEngine(
            model_path=str(settings.models_dir / "whisper_cpp" / "ggml-model.bin"),
        ),
        "whisper_api": lambda: WhisperAPIEngine(
            api_key=settings.openai_whisper_api_key, base_url=settings.whisper_api_base_url,
        ),
        "vosk": lambda: VoskEngine(
            model_path=str(settings.models_dir / "vosk"), work_dir=settings.work_dir,
        ),
    }

    engines = []
    for name in settings.asr_engine_chain:
        factory = registry.get(name)
        if factory is None:
            logger.warning("Unknown ASR engine '%s' in configured chain, skipping", name)
            continue
        engines.append(factory())
    return engines
