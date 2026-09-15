"""Real (non-mocked) NLLB translation integration test. Downloads
facebook/nllb-200-distilled-600M on first run (network + ~2.4GB required) and translates
a real chunk into several unrelated target languages, proving dynamic/any-language-pair
support end to end rather than only through fakes.
"""
import pytest

from app.pipeline.interfaces import SourceChunk
from app.pipeline.stage8_translation.engines.nllb import NllbEngine
from app.pipeline.stage8_translation.translator import translate_chunks


@pytest.fixture(scope="module")
def nllb_engine(tmp_path_factory):
    cache_dir = tmp_path_factory.mktemp("nllb_models")
    return NllbEngine(model_name="facebook/nllb-200-distilled-600M", device="cpu", cache_dir=str(cache_dir))


@pytest.mark.integration
def test_nllb_available(nllb_engine):
    available, reason = nllb_engine.is_available()
    assert available, reason


@pytest.mark.integration
@pytest.mark.parametrize("target_lang,expected_substring", [
    ("es", "hola"),
    ("fr", "bonjour"),
    ("de", "hallo"),
])
def test_nllb_translates_across_dynamic_language_pairs(nllb_engine, target_lang, expected_substring):
    chunk = SourceChunk(chunk_id="c1", source_text="Hello, how are you today?", word_span=(0.0, 2.0),
                         source_segment_ids=["s1"])

    result = translate_chunks([chunk], source_language="en", target_language=target_lang, engines=[nllb_engine])

    assert result[0].engine == "nllb"
    assert result[0].target_language == target_lang
    assert result[0].translated_text.strip() != ""
    assert result[0].translated_text.strip() != chunk.source_text
    assert expected_substring in result[0].translated_text.lower()


@pytest.mark.integration
def test_nllb_preserves_proper_nouns_reasonably(nllb_engine):
    chunk = SourceChunk(chunk_id="c1", source_text="My name is Anthropic and I live in California.",
                         word_span=(0.0, 3.0), source_segment_ids=["s1"])

    result = translate_chunks([chunk], source_language="en", target_language="es", engines=[nllb_engine])

    translated_lower = result[0].translated_text.lower()
    assert "anthropic" in translated_lower
    assert "california" in translated_lower


@pytest.mark.integration  # needs torch, but no model download — mocked at the model boundary
def test_no_repeat_ngram_size_and_num_beams_are_wired_through_to_generation():
    """Proves the config values configured on NllbEngine really reach `model.generate()`
    — a real degenerate-repetition-loop input is not reliably reproducible in a small test
    model (that failure mode was originally observed on a 1.3B model with a specific real
    input), so this verifies the wiring directly instead: a fake model records the exact
    kwargs `_generate` passes it.
    """
    engine = NllbEngine(no_repeat_ngram_size=7, num_beams=3)
    captured = {}

    class FakeModel:
        def generate(self, **kwargs):
            captured.update(kwargs)
            return "fake-generated-ids"

    engine._model = FakeModel()
    engine._tokenizer = object()
    engine._device = "cpu"

    engine._generate(inputs={}, target_token_id=42, max_new_tokens=100)

    assert captured["no_repeat_ngram_size"] == 7
    assert captured["num_beams"] == 3
    assert captured["forced_bos_token_id"] == 42
    assert captured["max_new_tokens"] == 100


@pytest.mark.integration
def test_glossary_protects_a_name_through_real_translation(nllb_engine):
    """A nonsense out-of-vocabulary name NLLB would otherwise be free to mangle,
    transliterate, or drop — protecting it end to end through a real translation call
    proves glossary.py's placeholder mechanism actually survives real tokenization/
    generation, not just the string-substitution unit tests."""
    from app.pipeline.stage8_translation.glossary import Entity, build_glossary

    glossary_map = build_glossary([Entity(canonical="Zzyzxlon", surface_forms=["Zzyzxlon"])])
    chunk = SourceChunk(chunk_id="c1", source_text="Zzyzxlon walked into the room and said hello to everyone.",
                         word_span=(0.0, 3.0), source_segment_ids=["s1"])

    result = translate_chunks([chunk], source_language="en", target_language="es",
                               engines=[nllb_engine], glossary_map=glossary_map)

    assert "zzyzxlon" in result[0].translated_text.lower()
