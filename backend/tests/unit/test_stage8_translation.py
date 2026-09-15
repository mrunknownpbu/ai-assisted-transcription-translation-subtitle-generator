import pytest

from app.pipeline.interfaces import SourceChunk
from app.pipeline.stage8_translation.engines.base import TranslationEngineError
from app.pipeline.stage8_translation.language_codes import UnsupportedLanguageError, resolve_flores_code
from app.pipeline.stage8_translation.translator import TranslationUnavailableError, translate_chunks


class FakeTranslationEngine:
    def __init__(self, name, available=True, reason=None, raises=False, translation_fn=None):
        self.name = name
        self._available = available
        self._reason = reason
        self._raises = raises
        self._translation_fn = translation_fn or (lambda text, s, t: f"[{t}]{text}")
        self.model_version = f"{name}:test"

    def is_available(self):
        return self._available, self._reason

    def translate(self, text, source_flores, target_flores):
        if self._raises:
            raise TranslationEngineError(f"{self.name} exploded")
        return self._translation_fn(text, source_flores, target_flores)


def _chunk(chunk_id="chunk_00000", text="hello there"):
    return SourceChunk(chunk_id=chunk_id, source_text=text, word_span=(0.0, 1.0), source_segment_ids=["seg_00000"])


# --- language code resolution ---

@pytest.mark.unit
@pytest.mark.parametrize("user_input,expected", [
    ("es", "spa_Latn"), ("Spanish", "spa_Latn"), ("FR", "fra_Latn"),
    ("japanese", "jpn_Jpan"), ("zh", "zho_Hans"),
])
def test_resolve_common_languages(user_input, expected):
    assert resolve_flores_code(user_input) == expected


@pytest.mark.unit
def test_resolve_direct_flores_tag_passthrough_for_uncommon_language():
    assert resolve_flores_code("ban_Latn") == "ban_Latn"


@pytest.mark.unit
def test_resolve_unknown_language_raises_clear_error():
    with pytest.raises(UnsupportedLanguageError):
        resolve_flores_code("not-a-real-language")


# --- fallback orchestration, mirrors ASR fallback behavior ---

@pytest.mark.unit
def test_translate_chunks_uses_primary_engine_when_available():
    engine = FakeTranslationEngine("nllb")
    result = translate_chunks([_chunk()], "en", "es", [engine])
    assert result[0].engine == "nllb"
    assert result[0].target_language == "es"
    assert result[0].translated_text == "[spa_Latn]hello there"


@pytest.mark.unit
def test_translate_chunks_falls_back_when_primary_unavailable():
    primary = FakeTranslationEngine("nllb", available=False, reason="not installed")
    fallback = FakeTranslationEngine("api_engine")
    result = translate_chunks([_chunk()], "en", "fr", [primary, fallback])
    assert result[0].engine == "api_engine"


@pytest.mark.unit
def test_translate_chunks_falls_back_when_primary_raises():
    primary = FakeTranslationEngine("nllb", raises=True)
    fallback = FakeTranslationEngine("api_engine")
    result = translate_chunks([_chunk()], "en", "de", [primary, fallback])
    assert result[0].engine == "api_engine"


@pytest.mark.unit
def test_translate_chunks_all_engines_exhausted_raises():
    with pytest.raises(TranslationUnavailableError):
        translate_chunks([_chunk()], "en", "es", [FakeTranslationEngine("nllb", raises=True)])


@pytest.mark.unit
def test_translate_multiple_dynamic_target_languages_in_sequence():
    """Confirms no hardcoded target-language list: arbitrary language pairs work back to
    back against the same engine instance."""
    engine = FakeTranslationEngine("nllb")
    for target in ("es", "de", "ja", "ar", "sw"):
        result = translate_chunks([_chunk()], "en", target, [engine])
        assert result[0].target_language == target
