import pytest

from app.pipeline.interfaces import CanonicalSegment, CanonicalTranscript
from app.pipeline.stage7_normalization.normalizer import normalize_text, normalize_transcript


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [
    ("hello   world", "hello world"),
    ("hello ,world", "hello, world"),
    ("hello,world", "hello, world"),
    ("  leading and trailing  ", "leading and trailing"),
])
def test_universal_whitespace_and_punctuation_cleanup(raw, expected):
    assert normalize_text(raw, language="en") == expected


@pytest.mark.unit
def test_english_standalone_i_is_capitalized():
    assert normalize_text("i think i can", language="en") == "I think I can"


@pytest.mark.unit
def test_standalone_i_rule_does_not_touch_other_words():
    assert normalize_text("this is fine", language="en") == "this is fine"


@pytest.mark.unit
def test_non_english_language_skips_english_specific_rules():
    # "i" capitalization is an English-only orthographic convention; must not apply elsewhere.
    assert normalize_text("i amo la vida", language="es") == "i amo la vida"


@pytest.mark.unit
def test_normalization_never_mutates_original_text_field():
    seg = CanonicalSegment(segment_id="s1", start=0.0, end=1.0, text="i   said ,hi", normalized_text="i   said ,hi",
                            words=[], avg_confidence=0.9)
    transcript = CanonicalTranscript(segments=[seg], provenance={})

    normalize_transcript(transcript, language="en")

    assert seg.text == "i   said ,hi"  # original utterance record preserved verbatim
    assert seg.normalized_text == "I said, hi"


@pytest.mark.unit
def test_normalization_does_not_rewrite_wording_or_drop_filler_words():
    seg = CanonicalSegment(segment_id="s1", start=0.0, end=1.0, text="um i guess so", normalized_text="um i guess so",
                            words=[], avg_confidence=0.9)
    transcript = CanonicalTranscript(segments=[seg], provenance={})

    normalize_transcript(transcript, language="en")

    assert "um" in seg.normalized_text  # filler words are speaker intent, never removed
