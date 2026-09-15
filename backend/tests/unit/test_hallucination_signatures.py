import json

import pytest

from app.pipeline.stage6_hallucination_defense.signatures import load_signatures, matching_weight


@pytest.mark.unit
def test_bundled_default_signatures_load():
    signatures = load_signatures()
    assert len(signatures) >= 3
    assert any(s.id == "tr-subtitle-credit-altyazi" for s in signatures)
    assert any(s.id == "zh-subtitle-credit-zimuzu" for s in signatures)


@pytest.mark.unit
def test_matching_weight_catches_simplified_and_traditional_chinese_fansub_credit():
    signatures = load_signatures()
    weight_simplified, sig_id = matching_weight("感谢观看 中文字幕组制作", "zh", signatures)
    assert weight_simplified > 0
    assert sig_id == "zh-subtitle-credit-zimuzu"
    weight_traditional, _ = matching_weight("中文字幕組", "zh", signatures)
    assert weight_traditional > 0


@pytest.mark.unit
def test_generic_url_signature_matches_regardless_of_detected_language():
    """Regression test for a real case: a Canadian Chinese-language newspaper's own credit
    line ('MING PAO CANADA www.MINGPAO.com') hallucinated into a Chinese-audio episode's
    end credits. A URL isn't spoken dialogue in any language, so this signature must fire
    no matter what language the transcript was detected as."""
    signatures = load_signatures()
    for language in ("zh", "tr", "en", "es"):
        weight, sig_id = matching_weight("MING PAO CANADA www.MINGPAO.com", language, signatures)
        assert weight > 0, f"expected a match for language={language!r}"
        assert sig_id == "generic-credit-url"


@pytest.mark.unit
def test_generic_credit_vocabulary_signature_catches_english_broadcaster_credit_in_any_language():
    """Regression test for a real case: 'Yo-Yo Television Series Exclusive' (a broadcaster
    self-credit) hallucinated into the same Chinese-audio episode's end credits -- fansub/
    broadcaster credit vocabulary conventionally stays in English regardless of the show's
    actual spoken language."""
    signatures = load_signatures()
    weight, sig_id = matching_weight("Yo-Yo Television Series Exclusive", "zh", signatures)
    assert weight > 0
    assert sig_id == "generic-credit-vocabulary"


@pytest.mark.unit
def test_generic_credit_vocabulary_catches_bare_subtitles_group_without_by():
    """Regression test: a follow-up run of the same real episode hallucinated the same
    underlying fansub-credit concept as English text ('Subtitles group') instead of the
    Chinese '字幕组' zh-subtitle-credit-zimuzu already catches -- confirming ASR can
    hallucinate the same watermark in either language for the same audio. The original
    pattern only matched 'subtitles by X' and let this bare phrasing straight through."""
    signatures = load_signatures()
    for phrase in ("Subtitles group", "Subtitle Team", "Fansub"):
        weight, sig_id = matching_weight(phrase, "en", signatures)
        assert weight > 0, f"expected a match for {phrase!r}"
        assert sig_id == "generic-credit-vocabulary"


@pytest.mark.unit
def test_generic_signatures_do_not_match_ordinary_dialogue():
    signatures = load_signatures()
    weight, sig_id = matching_weight("I can't believe you would do this to me", "en", signatures)
    assert weight == 0.0
    assert sig_id is None


@pytest.mark.unit
def test_matching_weight_returns_zero_for_no_match():
    signatures = load_signatures()
    weight, sig_id = matching_weight("completely unrelated english text", "tr", signatures)
    assert weight == 0.0
    assert sig_id is None


@pytest.mark.unit
def test_matching_weight_is_case_insensitive():
    signatures = load_signatures()
    weight, sig_id = matching_weight("BU ALTYAZI EKIBI", "tr", signatures)
    assert weight > 0
    assert sig_id == "tr-subtitle-credit-altyazi"


@pytest.mark.unit
def test_matching_weight_gated_by_language():
    signatures = load_signatures()
    weight, _ = matching_weight("bu altyazı ekibi", "en", signatures)
    assert weight == 0.0


@pytest.mark.unit
def test_missing_signatures_file_returns_empty_list(tmp_path):
    assert load_signatures(tmp_path / "does_not_exist.json") == []


@pytest.mark.unit
def test_custom_signatures_file_is_loaded(tmp_path):
    custom_path = tmp_path / "custom.json"
    custom_path.write_text(json.dumps({"signatures": [
        {"id": "custom-1", "language": "en", "pattern": "test pattern", "category": "test",
         "weight": 0.6, "description": "unit test fixture"},
    ]}))

    signatures = load_signatures(custom_path)
    weight, sig_id = matching_weight("this is a test pattern here", "en", signatures)

    assert weight == 0.6
    assert sig_id == "custom-1"


@pytest.mark.unit
def test_highest_weight_wins_when_multiple_signatures_match(tmp_path):
    custom_path = tmp_path / "custom.json"
    custom_path.write_text(json.dumps({"signatures": [
        {"id": "low", "language": "en", "pattern": "foo", "category": "x", "weight": 0.3, "description": "d"},
        {"id": "high", "language": "en", "pattern": "foo bar", "category": "x", "weight": 0.8, "description": "d"},
    ]}))

    signatures = load_signatures(custom_path)
    weight, sig_id = matching_weight("foo bar baz", "en", signatures)

    assert weight == 0.8
    assert sig_id == "high"
