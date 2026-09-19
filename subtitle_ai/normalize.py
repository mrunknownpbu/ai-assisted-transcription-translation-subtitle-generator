"""Bounded, provenance-aware source-text normalization.

Confirmed motivating case: Whisper large-v3 consistently transcribes the
Turkish word "petunya" (a flower name) as "Petunia" -- the anglicized
spelling. Confirmed against a real human-produced reference subtitle
(srt-output/Sen Çal Kapimi 1. Bölüm.srt), which spells it "petunya" at the
exact timestamp Whisper renders "Petunia" -- same referent, same audio,
wrong transliteration, not a content/meaning error.

This is deliberately NOT a general "AI disagrees with a human reference,
so fix it" engine -- every rule here is a name-only transliteration
correction that requires the same kind of independent confirmation this
one had (a real reference transcript proving the intended spelling), never
guessed, never auto-learned from unverified reference differences. Each
correction records a Correction (transcript.py) on the affected Word, so
`original_text` always preserves what Whisper actually said.
"""

from __future__ import annotations

import re

from transcript import Correction, CorrectionKind, Word

# A correction below this confidence is a candidate, not a decision: the
# word is preserved unchanged. Added 2026-09-19 alongside a real Turkish
# entity-protection bug fix (glossary.py's İ-casefold defect) found while
# scoping broader Turkish-language work -- this module's own rule (below)
# already only ever adds a correction backed by independent confirmation
# (a real reference transcript), so every rule that meets THIS module's
# bar naturally clears a high threshold too. The gate exists so a lower-
# confidence rule can be added later (e.g. a spoken/colloquial form seen
# in real output but not yet independently confirmed the way the petunya
# case was) without it silently behaving as an unconditional rewrite.
MIN_NORMALIZATION_CONFIDENCE = 0.85

# rule_id -> (compiled whole-word pattern, correct spelling, evidence note,
# confidence). Adding a new rule here is the ONLY step required -- no code
# change elsewhere reads or needs to know about individual words. See this
# module's docstring: a rule here is a name/word TRANSLITERATION
# correction requiring the same independent confirmation the petunya case
# had (a real reference transcript proving the intended spelling) -- never
# guessed, never a general "normalize colloquial speech" mechanism. A
# confidence below MIN_NORMALIZATION_CONFIDENCE preserves the original
# text instead of applying the rule.
_RULES: dict[str, tuple[re.Pattern, str, str, float]] = {
    "tr-petunia-petunya": (
        re.compile(r"\bpetunia\b", re.IGNORECASE), "petunya",
        "Confirmed against srt-output/Sen Çal Kapimi 1. Bölüm.srt: human "
        "reference spells this word 'petunya' at 136.27-140.48s, the exact "
        "timestamp Whisper renders 'Petunia'.",
        1.0,
    ),
}


def _case_matched_replacement(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement.capitalize()
    return replacement


def normalize_word(word: Word, language: str) -> Word:
    """Apply every rule scoped to `language` to one word's text. Runs
    after alignment and after hallucination detection, so it never
    affects timing and never masks a hallucination finding -- it only
    ever respells a word ASR already placed correctly in time."""
    if language != "tr":
        return word
    text = word.text
    for rule_id, (pattern, replacement, evidence, confidence) in _RULES.items():
        if confidence < MIN_NORMALIZATION_CONFIDENCE:
            continue
        match = pattern.search(text)
        if not match:
            continue
        corrected = _case_matched_replacement(match.group(0), replacement)
        text = pattern.sub(corrected, text)
        word.corrections.append(Correction(
            kind=CorrectionKind.NORMALIZATION, rule_id=rule_id,
            evidence=evidence, confidence=confidence))
    word.text = text
    return word


def normalize_transcript_words(words: list[Word], language: str) -> list[Word]:
    return [normalize_word(w, language) for w in words]
