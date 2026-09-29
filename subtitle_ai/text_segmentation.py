"""Sentence/clause-boundary splitting and line-wrapping, ported from
segmentation_target.py's proven "best-scoring boundary near the midpoint,
never an arbitrary word-count fraction" design so segmentation_source.py
(source-language, e.g. Turkish) can reuse the same algorithm instead of
segmentation_source.py's old flat 84-char/12-char gate.

segmentation_target.py is left untouched (its own English-specific
_TITLE_ABBREVIATIONS/_STRONG_CONJUNCTIONS/_CLINGY_WORDS and test suite are
already correct and validated on real translated output) -- this module
is the shared, LANGUAGE-PARAMETERIZED version both source and target
segmentation can converge on over time, not a replacement for it today.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_LINE_CHARS = 42
MAX_LINES = 2
MAX_CUE_CHARS = MAX_LINE_CHARS * MAX_LINES

_SENTENCE_END = re.compile(
    r"[.!?…]['\"»)\]」』）】]*(?:\s|$)|[。？！]['\"»)\]」』）】]*")
_CLAUSE_END = re.compile(r"[,;:、]['\"»)\]」』）】]*$")
_STRIP_PUNCT = ".,!?;:\"'()[]…、。？！「」『』（）【】"


@dataclass(frozen=True)
class SplitLexicon:
    """Language-specific word lists the boundary-scoring heuristic uses as
    a TIE-BREAKER (see _boundary_score) -- punctuation is always the
    primary signal. An empty set means "no lexical tie-break for this
    language, punctuation only", not "language unsupported"."""
    title_abbreviations: frozenset[str] = field(default_factory=frozenset)
    strong_conjunctions: frozenset[str] = field(default_factory=frozenset)
    clingy_words: frozenset[str] = field(default_factory=frozenset)


ENGLISH = SplitLexicon(
    title_abbreviations=frozenset({"mr", "mrs", "ms", "mx", "dr", "prof", "sr", "jr", "st"}),
    strong_conjunctions=frozenset({
        "and", "but", "so", "because", "if", "when", "while", "although",
        "though", "since", "unless", "then", "yet", "or", "nor"}),
    clingy_words=frozenset({
        "a", "an", "the", "my", "your", "his", "her", "its", "our", "their",
        "this", "that", "these", "those", "am", "is", "are", "was", "were",
        "be", "been", "being", "do", "does", "did", "have", "has", "had",
        "will", "would", "shall", "should", "can", "could", "may", "might", "must"}))

# Punctuation-based scoring (sentence/clause end) is language-agnostic and
# does the heavy lifting either way; this only adds a small conjunction
# bonus. Deliberately no Turkish clingy-word list: Turkish case/possessive
# marking is mostly suffixed onto the word itself rather than a separate
# leading particle the way English articles/auxiliaries are, and guessing
# at a list risked being wrong more often than useful -- left empty rather
# than speculative.
TURKISH = SplitLexicon(
    title_abbreviations=frozenset({"dr", "prof", "sn", "av", "doç"}),
    strong_conjunctions=frozenset({"ve", "ama", "fakat", "çünkü", "ancak", "veya", "yoksa", "oysa"}))


def _is_title_abbreviation_period(text: str, period_pos: int, lexicon: SplitLexicon) -> bool:
    word_start = period_pos
    while word_start > 0 and text[word_start - 1].isalpha():
        word_start -= 1
    return text[word_start:period_pos].casefold() in lexicon.title_abbreviations


def split_sentences(text: str, lexicon: SplitLexicon = ENGLISH) -> list[str]:
    """Split on sentence-ending punctuation, keeping the punctuation
    attached to the sentence it closes. A "." closing a known title
    abbreviation (`lexicon.title_abbreviations`) is never a sentence end."""
    pieces = []
    i = 0
    for m in _SENTENCE_END.finditer(text):
        end = m.end()
        if text[m.start()] == "." and _is_title_abbreviation_period(text, m.start(), lexicon):
            continue
        piece = text[i:end].strip()
        if piece:
            pieces.append(piece)
        i = end
    tail = text[i:].strip()
    if tail:
        pieces.append(tail)
    return pieces or ([text.strip()] if text.strip() else [])


def _boundary_score(prev_word: str, next_word: str, lexicon: SplitLexicon = ENGLISH) -> float:
    score = 0.0
    if _SENTENCE_END.search(prev_word + " "):
        score += 4.0
    elif _CLAUSE_END.search(prev_word):
        score += 2.0
    next_bare = next_word.strip(_STRIP_PUNCT).lower()
    if next_bare in lexicon.strong_conjunctions:
        score += 1.0
    prev_bare = prev_word.strip(_STRIP_PUNCT).lower()
    if prev_bare in lexicon.clingy_words:
        score -= 3.0
    return score


def split_long_piece(piece: str, max_chars: int = MAX_CUE_CHARS, lexicon: SplitLexicon = ENGLISH) -> list[str]:
    """A single sentence too long for one cue: split at the best-scoring
    clause/conjunction boundary near the midpoint, recursively, never at
    an arbitrary word-count fraction."""
    if len(piece) <= max_chars:
        return [piece]
    words = piece.split()
    if len(words) < 2:
        return [piece]
    best_pos, best_score = None, None
    for pos in range(1, len(words)):
        left = " ".join(words[:pos])
        right = " ".join(words[pos:])
        if len(left) > max_chars or len(right) > max_chars:
            continue
        score = _boundary_score(words[pos - 1], words[pos], lexicon) - abs(pos - len(words) / 2) * 0.05
        if best_score is None or score > best_score:
            best_score, best_pos = score, pos
    if best_pos is None:
        # No split keeps both halves under budget -- minimise the longer
        # half rather than leaving the sentence whole and over-length.
        best_pos = min(range(1, len(words)),
                       key=lambda p: max(len(" ".join(words[:p])), len(" ".join(words[p:]))))
    left, right = " ".join(words[:best_pos]), " ".join(words[best_pos:])
    return split_long_piece(left, max_chars, lexicon) + split_long_piece(right, max_chars, lexicon)


def wrap_lines(text: str, max_line_chars: int = MAX_LINE_CHARS, lexicon: SplitLexicon = ENGLISH) -> list[str]:
    if len(text) <= max_line_chars:
        return [text]
    words = text.split()
    if len(words) < 2:
        # A single unspaced token longer than max_line_chars (e.g.
        # garbled ASR output, a URL) can't be cut anywhere -- return it
        # as one over-length line rather than crash. Real production
        # failure this fixes (2026-09-28, Hammer Session! S01E01,
        # "ValueError: min() iterable argument is empty"): `range(1, 1)`
        # is empty, and the fallback below used to call min() on it
        # unconditionally. Same guard split_long_piece() already has.
        return [text]
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) > max_line_chars or len(b) > max_line_chars:
            continue
        cost = abs(len(a) - len(b)) - _boundary_score(a, b, lexicon) * 2
        if best is None or cost < best[0]:
            best = (cost, [a, b])
    if best:
        return best[1]
    mid = min(range(1, len(words)),
             key=lambda i: max(len(" ".join(words[:i])), len(" ".join(words[i:]))))
    return [" ".join(words[:mid]), " ".join(words[mid:])]
