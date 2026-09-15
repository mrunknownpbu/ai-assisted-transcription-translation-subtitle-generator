import pytest

from app.evaluation.wer import word_error_rate


@pytest.mark.unit
def test_identical_text_has_zero_wer():
    result = word_error_rate("Bugün hava çok güzel.", "Bugün hava çok güzel.")
    assert result.wer == 0.0
    assert result.matches == 4
    assert result.substitutions == result.deletions == result.insertions == 0


@pytest.mark.unit
def test_punctuation_and_case_differences_are_ignored():
    # normalize_for_comparison lowercases and strips punctuation -- WER should reflect real
    # word-content differences only, not formatting differences between two independently
    # authored subtitle tracks.
    result = word_error_rate("Bugün hava çok güzel.", "bugün HAVA, çok güzel")
    assert result.wer == 0.0


@pytest.mark.unit
def test_single_substitution_is_counted_once():
    result = word_error_rate("Cenk geldi mi?", "Savaş geldi mi?")
    assert result.substitutions == 1
    assert result.deletions == 0
    assert result.insertions == 0
    assert result.wer == pytest.approx(1 / 3)


@pytest.mark.unit
def test_pure_deletion_missing_words_in_hypothesis():
    result = word_error_rate("bir iki üç dört", "bir dört")
    assert result.deletions == 2
    assert result.substitutions == 0
    assert result.insertions == 0
    assert result.wer == pytest.approx(2 / 4)


@pytest.mark.unit
def test_pure_insertion_extra_words_in_hypothesis():
    result = word_error_rate("bir dört", "bir iki üç dört")
    assert result.insertions == 2
    assert result.substitutions == 0
    assert result.deletions == 0
    assert result.wer == pytest.approx(2 / 2)


@pytest.mark.unit
def test_empty_reference_and_empty_hypothesis_is_trivially_perfect():
    result = word_error_rate("", "")
    assert result.wer == 0.0
    assert result.reference_word_count == 0


@pytest.mark.unit
def test_empty_reference_with_nonempty_hypothesis_is_worst_case():
    result = word_error_rate("", "tamamen alakasız metin")
    assert result.wer == 1.0
    assert result.insertions == 3


@pytest.mark.unit
def test_frequent_short_words_are_not_treated_as_junk():
    # Regression guard for the difflib autojunk gotcha: a word repeated often enough to
    # exceed autojunk's default 1% popularity threshold must still align correctly rather
    # than being silently excluded from matching.
    reference = " ".join(["evet"] * 150 + ["hayır", "belki"])
    hypothesis = " ".join(["evet"] * 150 + ["hayır", "belki"])
    result = word_error_rate(reference, hypothesis)
    assert result.wer == 0.0
    assert result.matches == 152


@pytest.mark.unit
def test_circumflex_spelling_variants_are_not_counted_as_errors():
    # "herhâlde" and "herhalde" are the same word under modern Turkish orthography -- an
    # optional circumflex, not a different letter. Confirmed real case from a 5-episode
    # sample where this alone accounted for 24 "substitutions" that were not real errors.
    result = word_error_rate("Herhâlde âşık oldu.", "Herhalde aşık oldu.")
    assert result.wer == 0.0


@pytest.mark.unit
def test_dotted_and_dotless_i_are_not_folded_since_they_are_distinct_turkish_letters():
    # Unlike the circumflex, ı/i and İ/i are genuinely different letters in Turkish (e.g.
    # "kız" vs "kiz" are different words) -- folding these would hide real content errors,
    # not orthography noise, so this must NOT be treated the same as the circumflex case.
    result = word_error_rate("kız", "kiz")
    assert result.wer == 1.0
    assert result.substitutions == 1


@pytest.mark.unit
def test_proper_noun_apostrophe_convention_is_not_counted_as_an_error():
    # "Eda'cığım" (proper noun + suffix) and "edacığım" (treated as a plain word once
    # lowercased) are the same word under Turkish orthography's apostrophe convention.
    result = word_error_rate("Eda'cığım nerede?", "Edacığım nerede?")
    assert result.wer == 0.0


@pytest.mark.unit
def test_cue_boundary_misalignment_does_not_penalize_whole_transcript_wer():
    """The real motivation for this module: metrics.py's per-cue chrF scores a short,
    correct utterance near zero when it only partially overlaps a wider reference cue
    covering a multi-speaker exchange. Concatenating full transcripts sidesteps this --
    the same words in the same order should score perfectly regardless of how each side
    chopped them into cues."""
    reference_cues = ["-Evet.", "-Evet.", "Rüzgar nereden gelirse."]
    hypothesis_cues = ["Evet.", "Evet.", "Rüzgar nereden gelirse."]
    result = word_error_rate(" ".join(reference_cues), " ".join(hypothesis_cues))
    assert result.wer == 0.0
