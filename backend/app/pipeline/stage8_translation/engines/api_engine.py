"""Optional fallback: an external LLM/translation API, disabled by default (no API key
configured). Kept behind the same TranslationEngine contract as NLLB so a user who wants
higher-fidelity idiom/nuance handling for a specific job can opt in without any pipeline
changes — this is the pluggable-engine extension point called out in the architecture
plan, not a fully productized integration.
"""
from __future__ import annotations

import logging

from app.pipeline.stage8_translation.engines.base import TranslationEngineError

logger = logging.getLogger("subtitle_platform.pipeline.translation.api")


class ApiTranslationEngine:
    name = "api_engine"

    def __init__(self, api_key: str | None, base_url: str = "https://api.openai.com/v1", model: str = "gpt-4o-mini"):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    @property
    def model_version(self) -> str:
        return f"api:{self.model}"

    def is_available(self) -> tuple[bool, str | None]:
        if not self.api_key:
            return False, "no API key configured (SUBTITLE_TRANSLATION_API_KEY unset)"
        try:
            import openai  # noqa: F401
        except ImportError as exc:
            return False, f"openai package not importable: {exc}"
        return True, None

    def translate(self, text: str, source_flores: str, target_flores: str) -> str:
        if not text.strip():
            return ""
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            prompt = (
                f"Translate the following subtitle text from language code '{source_flores}' to "
                f"'{target_flores}'. Preserve proper names, places, and specialized terminology. "
                f"Return only the translated text, nothing else.\n\n{text}"
            )
            response = client.chat.completions.create(
                model=self.model, messages=[{"role": "user", "content": prompt}], temperature=0.2,
            )
            return response.choices[0].message.content.strip()
        except Exception as exc:
            raise TranslationEngineError(f"API translation failed ({source_flores}->{target_flores}): {exc}") from exc
