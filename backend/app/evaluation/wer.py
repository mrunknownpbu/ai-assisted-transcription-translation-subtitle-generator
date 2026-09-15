"""Whole-transcript word error rate: an alternative to metrics.py's per-cue chrF that is
immune to cue-boundary misalignment between two independently-segmented subtitle tracks.

Confirmed real cause of misleadingly low per-cue scores: our source segmentation emits a
short, correct utterance ("Evet.") as its own tightly-timed cue, while an independently
authored reference track groups a whole multi-speaker exchange into one wider cue
("-Evet. -Evet."). metrics.py's time-overlap alignment then compares the short cue against
the entire wider block and scores it near zero, even though the short cue's own content was
right -- a measurement artifact of differing segmentation granularity, not a transcription
error. Concatenating each track into one ordered word stream and aligning the whole thing
sidesteps cue boundaries entirely.

A true O(n*m) edit-distance DP is infeasible in pure Python at whole-episode word counts
(10k-20k words per side); difflib.SequenceMatcher finds long matching runs in practical time
and is the same technique commonly used for text-based WER outside of audio-forced-alignment
contexts.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from app.evaluation.metrics import normalize_for_comparison

# Circumflexed-vowel spelling is an optional, interchangeable convention in modern Turkish
# (e.g. "herhâlde"/"herhalde", "âşık"/"aşık") -- not a phonemic distinction the way ı/i or
# İ/i are (those genuinely change the word and must NOT be folded here). Confirmed via a
# real 5-episode sample: these accounted for a meaningful slice of "substitutions" between
# our ASR and an independently-authored reference that were really just spelling-convention
# differences, not transcription errors -- diluting the real error signal WER exists to
# surface. Only the three circumflexed vowels are folded, deliberately narrow.
_CIRCUMFLEX_FOLD = str.maketrans("âÂîÎûÛ", "aAiIuU")


@dataclass(frozen=True)
class WerResult:
    substitutions: int
    deletions: int
    insertions: int
    matches: int
    reference_word_count: int
    wer: float  # (substitutions + deletions + insertions) / reference_word_count


def _words(text: str) -> list[str]:
    normalized = normalize_for_comparison(text)
    if not normalized:
        return []
    # Turkish orthography adds an apostrophe before a case suffix on a proper noun
    # ("Eda'cığım", "otel'e" when "otel" here is being used as if a name) but not on a
    # common noun ("otele"/"edacığım" as plain nouns) -- both spellings are the same word,
    # and normalize_for_comparison already lowercases away the capitalization that would
    # normally signal which convention applies, so stripping apostrophes entirely (rather
    # than trying to reconstruct which convention each occurrence used) is the fix. This
    # can only merge two words together at an apostrophe -- normalize_for_comparison never
    # produces a bare `'` token on its own, so there is nothing else for this to affect.
    normalized = normalized.replace("'", "").translate(_CIRCUMFLEX_FOLD)
    return normalized.split()


def word_error_rate(reference_text: str, hypothesis_text: str) -> WerResult:
    ref_words = _words(reference_text)
    hyp_words = _words(hypothesis_text)
    if not ref_words:
        # No reference words: WER is conventionally undefined (0/0). Treat "hypothesis also
        # empty" as a perfect (trivial) match and any hypothesis text as pure insertion noise
        # scored as the worst case (1.0), rather than raising or dividing by zero.
        return WerResult(
            substitutions=0, deletions=0, insertions=len(hyp_words), matches=0,
            reference_word_count=0, wer=0.0 if not hyp_words else 1.0,
        )

    # autojunk=False matters here: difflib's default autojunk=True downweights elements
    # that look "too popular" (present in >1% of a long sequence) as probable junk, which
    # would misfire on genuinely frequent short words ("evet", "tamam", "hı") in real
    # dialogue rather than actual junk -- a well-known difflib gotcha for text alignment.
    matcher = SequenceMatcher(None, ref_words, hyp_words, autojunk=False)
    substitutions = deletions = insertions = matches = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        ref_span, hyp_span = i2 - i1, j2 - j1
        if tag == "equal":
            matches += ref_span
        elif tag == "replace":
            # A replace block of unequal length is treated as substituting the shorter
            # side's worth of words, with the remainder counted as pure insertion/deletion
            # -- the standard practical approximation used by text-based WER tools that
            # align via longest-matching-block search rather than true minimum-edit-distance
            # DP (which get_opcodes() does not directly expose a sub/ins/del split for).
            substitutions += min(ref_span, hyp_span)
            deletions += max(0, ref_span - hyp_span)
            insertions += max(0, hyp_span - ref_span)
        elif tag == "delete":
            deletions += ref_span
        elif tag == "insert":
            insertions += hyp_span

    total_errors = substitutions + deletions + insertions
    return WerResult(
        substitutions=substitutions, deletions=deletions, insertions=insertions,
        matches=matches, reference_word_count=len(ref_words),
        wer=total_errors / len(ref_words),
    )
