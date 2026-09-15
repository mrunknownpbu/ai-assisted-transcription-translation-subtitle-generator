"""Translation-quality QC — checks the *content* of a translation independent of subtitle
timing/formatting (that's `qc.py`'s job). Runs on Stage 8's `TranslatedChunk`s, comparing
each chunk's source text against its translated text, plus one cross-chunk check.

Every check logs a `QcFinding` for every chunk, pass or fail, same convention as `qc.py` —
these are annotative only: a flagged translation still ships in the output, just recorded,
consistent with the platform's false-negative-minimization stance (never silently drop
dialogue over a QC heuristic).
"""
from __future__ import annotations

import re
from collections import Counter

from app.pipeline.interfaces import QcFinding, QcReport, TranslatedChunk

_PLACEHOLDER_RE = re.compile(r"\bX[a-z]{2}\b")  # matches glossary.py's placeholder scheme (Xaa, Xab, ...)
_BRACKETS = {"(": ")", "[": "]", "{": "}"}

# Length-ratio thresholds only apply above a minimum source length — short strings have
# naturally wide length ratios across languages and would otherwise false-positive.
_SHORT_RATIO_MIN_SRC_LEN = 15
_SHORT_RATIO_THRESHOLD = 0.25
_LONG_RATIO_MIN_SRC_LEN = 5
_LONG_RATIO_THRESHOLD = 3.5

_MIN_REPEATED_TRIGRAM_COUNT = 3
_MIN_WORDS_FOR_REPETITION_CHECK = 9


def _unbalanced_brackets(text: str) -> bool:
    stack = []
    closing_to_opening = {v: k for k, v in _BRACKETS.items()}
    for ch in text:
        if ch in _BRACKETS:
            stack.append(ch)
        elif ch in closing_to_opening:
            if not stack or stack[-1] != closing_to_opening[ch]:
                return True
            stack.pop()
    return bool(stack)


def _degenerate_repetition(text: str) -> bool:
    """Trigram-repetition heuristic — a belt-and-suspenders detector independent of the
    generation-time `no_repeat_ngram_size` fix (nllb.py): if that fix is ever bypassed
    (a different engine, a config change), this still catches the same failure mode."""
    words = re.findall(r"\w+", text.lower())
    if len(words) < _MIN_WORDS_FOR_REPETITION_CHECK:
        return False
    counts = Counter(tuple(words[i:i + 3]) for i in range(len(words) - 2))
    return bool(counts) and max(counts.values()) >= _MIN_REPEATED_TRIGRAM_COUNT


def _assess_chunk(source: str, translated: str) -> QcFinding | None:
    if not translated.strip():
        return QcFinding(check="translation_non_empty", passed=False, reason="translation is empty")

    if _PLACEHOLDER_RE.search(translated):
        return QcFinding(check="translation_no_leaked_placeholder", passed=False,
                          reason=f"leaked glossary placeholder in output: {translated!r}")

    if _unbalanced_brackets(translated):
        return QcFinding(check="translation_balanced_brackets", passed=False, reason="unbalanced brackets in translation")

    src_len, tgt_len = len(source), len(translated)
    if src_len >= _SHORT_RATIO_MIN_SRC_LEN and tgt_len < src_len * _SHORT_RATIO_THRESHOLD:
        return QcFinding(check="translation_length_ratio", passed=False,
                          reason=f"translation suspiciously short: {tgt_len} chars vs source {src_len} chars")
    if src_len >= _LONG_RATIO_MIN_SRC_LEN and tgt_len > src_len * _LONG_RATIO_THRESHOLD:
        return QcFinding(check="translation_length_ratio", passed=False,
                          reason=f"translation suspiciously long: {tgt_len} chars vs source {src_len} chars")

    if _degenerate_repetition(translated):
        return QcFinding(check="translation_no_degenerate_repetition", passed=False,
                          reason="translation contains a repeated 3-word phrase suggesting a generation loop")

    return None


def run(chunks: list[TranslatedChunk], target_language: str | None = None) -> QcReport:
    findings: list[QcFinding] = []
    # casefolded translated text -> (first chunk_id, its source text) — catches a batch
    # collapse bug where two DIFFERENT source sentences produce the identical translation.
    seen_translations: dict[str, tuple[str, str]] = {}

    for chunk in chunks:
        failure = _assess_chunk(chunk.source_text, chunk.translated_text)
        if failure is not None:
            findings.append(QcFinding(check=failure.check, passed=False, reason=failure.reason, cue_id=chunk.chunk_id))
        else:
            findings.append(QcFinding(check="translation_content_ok", passed=True,
                                       reason="no translation-quality issues detected", cue_id=chunk.chunk_id))

        key = " ".join(chunk.translated_text.casefold().split())
        if len(key) >= 8:
            prior = seen_translations.get(key)
            if prior is None:
                seen_translations[key] = (chunk.chunk_id, chunk.source_text)
            elif prior[1] != chunk.source_text:
                findings.append(QcFinding(
                    check="translation_no_cross_chunk_duplicate", passed=False, cue_id=chunk.chunk_id,
                    reason=f"identical translated text also produced for chunk {prior[0]} from different source text",
                ))

    passed = all(f.passed for f in findings)
    return QcReport(stage="translation_qc", target_language=target_language, passed=passed, findings=findings)
