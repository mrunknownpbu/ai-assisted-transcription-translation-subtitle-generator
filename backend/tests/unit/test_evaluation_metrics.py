import pytest

from app.evaluation.metrics import (
    align_reference_text, chrf_score, evaluate_cues, normalize_for_comparison, word_overlap_f1,
)
from app.pipeline.stage11_qc_output.formatters.srt import ParsedSrtCue


@pytest.mark.unit
def test_normalize_strips_sdh_bracketed_sound_descriptions():
    assert normalize_for_comparison("[dramatic music] Hello there") == "hello there"


@pytest.mark.unit
def test_normalize_strips_speaker_labels():
    assert normalize_for_comparison("JOHN: I can't believe it") == "i can't believe it"


@pytest.mark.unit
def test_normalize_strips_leading_dialogue_dash():
    assert normalize_for_comparison("- Are you coming?") == "are you coming"


@pytest.mark.unit
def test_normalize_lowercases_and_collapses_whitespace():
    assert normalize_for_comparison("  Hello   WORLD  ") == "hello world"


@pytest.mark.unit
def test_normalize_strips_html_italics_tags_from_real_subtitle_markup():
    assert normalize_for_comparison("<i>You abolished the Soar Initiative.</i>") == "you abolished the soar initiative"


@pytest.mark.unit
def test_normalize_strips_a_mid_string_speaker_label_after_a_two_speaker_exchange_is_flattened():
    # Two-speaker cues are routinely flattened to one line/string before scoring (see
    # align_reference_text), so a second speaker's label must be stripped even when it's
    # no longer at the very start of the string.
    assert normalize_for_comparison("Hello? JOHN: I'm right here") == "hello i'm right here"


@pytest.mark.unit
def test_chrf_score_is_one_for_identical_text():
    assert chrf_score("The quick brown fox", "The quick brown fox") == pytest.approx(1.0)


@pytest.mark.unit
def test_chrf_score_is_zero_when_one_side_is_empty():
    assert chrf_score("", "something") == 0.0
    assert chrf_score("something", "") == 0.0


@pytest.mark.unit
def test_chrf_score_both_empty_is_perfect_match():
    assert chrf_score("", "") == 1.0


@pytest.mark.unit
def test_chrf_score_rewards_close_but_not_identical_text():
    close = chrf_score("The quick brown fox jumps", "The quick brown fox leaps")
    far = chrf_score("The quick brown fox jumps", "Completely unrelated sentence here")
    assert close > far
    assert 0.0 < close < 1.0


@pytest.mark.unit
def test_word_overlap_f1_identical_and_disjoint():
    assert word_overlap_f1("hello world", "hello world") == pytest.approx(1.0)
    assert word_overlap_f1("hello world", "completely different") == 0.0


@pytest.mark.unit
def test_align_reference_text_concatenates_overlapping_cues_in_time_order():
    hyp = ParsedSrtCue(index=1, start=10.0, end=20.0, text="hypothesis")
    ref = [
        ParsedSrtCue(index=2, start=15.0, end=18.0, text="second"),
        ParsedSrtCue(index=1, start=9.0, end=12.0, text="first"),
        ParsedSrtCue(index=3, start=25.0, end=30.0, text="unrelated, no overlap"),
    ]
    assert align_reference_text(hyp, ref) == "first second"


@pytest.mark.unit
def test_align_reference_text_empty_when_nothing_overlaps():
    hyp = ParsedSrtCue(index=1, start=100.0, end=110.0, text="x")
    ref = [ParsedSrtCue(index=1, start=0.0, end=5.0, text="y")]
    assert align_reference_text(hyp, ref) == ""


@pytest.mark.unit
def test_evaluate_cues_end_to_end_with_perfect_match():
    hyp = [ParsedSrtCue(index=1, start=0.0, end=5.0, text="Hello there")]
    ref = [ParsedSrtCue(index=1, start=0.0, end=5.0, text="Hello there")]
    report = evaluate_cues(hyp, ref)
    assert report.cue_count == 1
    assert report.matched_count == 1
    assert report.coverage == 1.0
    assert report.mean_chrf == pytest.approx(1.0)
    assert report.mean_word_f1 == pytest.approx(1.0)


@pytest.mark.unit
def test_evaluate_cues_treats_caption_only_reference_as_unmatched_not_a_zero_score():
    """Regression test for a real cross-check: comparing a job's output against an
    embedded human-made English track (stream 5) showed two very different hypothesis
    cues -- one a genuine hallucination ("Subtitles group"), one a correct translation --
    both scoring a hard 0.00 because the reference cue at that timestamp was purely an
    on-screen caption ("[Hengshi Air] [Three years later]"), which normalizes to nothing.
    An audio-only pipeline can never reproduce a caption nobody said aloud, so this must
    count as no comparable reference, not a real dialogue mismatch."""
    hyp = [ParsedSrtCue(index=1, start=0.0, end=5.0, text="Subtitles group")]
    ref = [ParsedSrtCue(index=1, start=0.0, end=5.0, text="[Hengshi Air] [Three years later]")]
    report = evaluate_cues(hyp, ref)
    assert report.matched_count == 0
    assert report.coverage == 0.0
    assert report.comparisons[0].reference_text == ""
    assert report.comparisons[0].chrf == 0.0


@pytest.mark.unit
def test_evaluate_cues_reports_unmatched_hypothesis_cues_separately():
    hyp = [
        ParsedSrtCue(index=1, start=0.0, end=5.0, text="Hello there"),
        ParsedSrtCue(index=2, start=100.0, end=105.0, text="No reference here"),
    ]
    ref = [ParsedSrtCue(index=1, start=0.0, end=5.0, text="Hello there")]
    report = evaluate_cues(hyp, ref)
    assert report.cue_count == 2
    assert report.matched_count == 1
    assert report.coverage == 0.5
    # mean_chrf is computed over matched cues only -- an unmatched cue must not silently
    # drag the average down as if it were a bad translation rather than a missing reference
    assert report.mean_chrf == pytest.approx(1.0)
    unmatched = next(c for c in report.comparisons if c.hypothesis_index == 2)
    assert unmatched.reference_text == ""
    assert unmatched.chrf == 0.0
