"""Stage 8: Translation orchestration.

Translates each Stage-9-produced SourceChunk (a pause-gap-grouped run of canonical
segments, giving the engine more context than a single short ASR segment) into the
user-specified target language, walking the configured engine chain with the same
skip-unavailable / fall-through-on-failure policy as ASR. Suppressed source segments are
never fed to translation — they were excluded from the transcript's dialogue text by
Stage 6, so nothing suppressed shows up in output subtitles either.
"""
from __future__ import annotations

import logging

from app.pipeline.interfaces import EngineAttempt, SourceChunk, TranslatedChunk
from app.pipeline.stage8_translation.engines.base import TranslationEngine, TranslationEngineError
from app.pipeline.stage8_translation.glossary import protect, recover_dropped_entities, restore
from app.pipeline.stage8_translation.language_codes import resolve_flores_code

logger = logging.getLogger("subtitle_platform.pipeline.translation")


class TranslationUnavailableError(RuntimeError):
    def __init__(self, attempts: list[EngineAttempt]):
        self.attempts = attempts
        summary = "; ".join(f"{a.engine}: {a.detail}" for a in attempts)
        super().__init__(f"All configured translation engines failed or were unavailable — {summary}")


def _translate_text_with_fallback(
    text: str, source_flores: str, target_flores: str, engines: list[TranslationEngine],
) -> tuple[str, str, str, list[EngineAttempt]]:
    """Returns (translated_text, engine_name, model_version, attempts)."""
    attempts: list[EngineAttempt] = []
    for engine in engines:
        available, reason = engine.is_available()
        if not available:
            attempts.append(EngineAttempt(engine=engine.name, outcome="skipped_unavailable", detail=reason))
            continue
        try:
            translated = engine.translate(text, source_flores, target_flores)
            attempts.append(EngineAttempt(engine=engine.name, outcome="succeeded"))
            model_version = getattr(engine, "model_version", engine.name)
            return translated, engine.name, model_version, attempts
        except TranslationEngineError as exc:
            logger.warning("Translation engine '%s' failed: %s", engine.name, exc)
            attempts.append(EngineAttempt(engine=engine.name, outcome="failed", detail=str(exc)))
            continue
    raise TranslationUnavailableError(attempts)


def translate_chunks(
    chunks: list[SourceChunk], source_language: str, target_language: str, engines: list[TranslationEngine],
    glossary_map: dict[str, tuple[str, str]] | None = None,
) -> list[TranslatedChunk]:
    """`glossary_map` (from `glossary.build_glossary`) is entirely optional — omitted or
    empty, this is a no-op and behavior is identical to a job with no glossary. When
    supplied: each chunk's source text is placeholder-protected before translation,
    restored after, and a purely-structural (no model call) entity-recovery pass reinserts
    any protected name the engine's output under-counts relative to the source — see
    `glossary.recover_dropped_entities` for why that recovery deliberately never calls a
    model.
    """
    if not engines:
        raise ValueError("No translation engines configured")

    source_flores = resolve_flores_code(source_language)
    target_flores = resolve_flores_code(target_language)
    glossary_map = glossary_map or {}

    translated_chunks: list[TranslatedChunk] = []
    for chunk in chunks:
        source_text = chunk.source_text
        protected_text = protect(source_text, glossary_map) if glossary_map else source_text

        translated_text, engine_name, model_version, attempts = _translate_text_with_fallback(
            protected_text, source_flores, target_flores, engines,
        )
        for attempt in attempts:
            if attempt.outcome == "skipped_unavailable":
                logger.info("Translation engine '%s' unavailable for chunk %s: %s",
                            attempt.engine, chunk.chunk_id, attempt.detail)

        if glossary_map:
            translated_text = restore(translated_text, glossary_map)
            translated_text = recover_dropped_entities(protected_text, translated_text, glossary_map)

        translated_chunks.append(TranslatedChunk(
            chunk_id=chunk.chunk_id, target_language=target_language, source_text=source_text,
            translated_text=translated_text, engine=engine_name, model_version=model_version,
            word_span=chunk.word_span,
        ))
    return translated_chunks


def build_default_engine_chain(settings) -> list[TranslationEngine]:
    from app.pipeline.stage8_translation.engines.api_engine import ApiTranslationEngine
    from app.pipeline.stage8_translation.engines.nllb import NllbEngine

    registry = {
        "nllb": lambda: NllbEngine(
            model_name=settings.nllb_model_name, cache_dir=str(settings.models_dir / "nllb"),
            no_repeat_ngram_size=settings.nllb_no_repeat_ngram_size, num_beams=settings.nllb_num_beams,
        ),
        "api_engine": lambda: ApiTranslationEngine(api_key=settings.translation_api_key),
    }

    engines = []
    for name in settings.translation_engine_chain:
        factory = registry.get(name)
        if factory is None:
            logger.warning("Unknown translation engine '%s' in configured chain, skipping", name)
            continue
        engines.append(factory())
    return engines
