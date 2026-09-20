"""Target-language segmentation: turns one translated span of text into
readable output cues.

This is the architectural change the rebuild exists to make. The prior
implementation split translated text by dividing it into buckets sized
proportionally to each *source* cue's word count, then let a separate
character-limit pass chop whatever landed in each bucket -- readability
was a downstream accident of source segmentation, not a goal target
segmentation optimized for.

Here, target segmentation reasons about the TARGET text on its own terms:
sentence boundaries first, clause boundaries and conjunctions when a
sentence alone is still too long, then times each resulting piece by its
own estimated reading duration (characters at a target reading speed),
scaled to fit the source envelope -- never by what fraction of the source
word count it happens to correspond to. A short English sentence
translated from a long Turkish one gets a duration proportional to how
long it takes to READ, not to how many Turkish words produced it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_LINE_CHARS = 42
MAX_LINES = 2
MAX_CUE_CHARS = MAX_LINE_CHARS * MAX_LINES
MIN_DURATION = 1.0
MAX_DURATION = 7.0
TARGET_CPS = 17.0

_SENTENCE_END = re.compile(r"[.!?…]['\"»)\]]*(?:\s|$)")
_CLAUSE_END = re.compile(r"[,;:]['\"»)\]]*$")
# English-only (this module only ever processes already-translated,
# target=English text, never source-language text) -- title abbreviations
# whose period must never count as a sentence end. Real bug (2026-09-20):
# rejoining two identical two-speaker dash lines that both translated to
# "- Good morning, Mr. Serkan." produced repeated "Mr." periods this
# splitter had no way to distinguish from real sentence ends, fragmenting
# "Mr." away from the name that follows it into its own short, garbled
# display cue.
_TITLE_ABBREVIATIONS = frozenset({"mr", "mrs", "ms", "mx", "dr", "prof", "sr", "jr", "st"})
_STRIP_PUNCT = ".,!?;:\"'()[]…"
_CLINGY_WORDS = frozenset({
    "a", "an", "the", "my", "your", "his", "her", "its", "our", "their",
    "this", "that", "these", "those", "am", "is", "are", "was", "were",
    "be", "been", "being", "do", "does", "did", "have", "has", "had",
    "will", "would", "shall", "should", "can", "could", "may", "might", "must",
})
_STRONG_CONJUNCTIONS = frozenset({
    "and", "but", "so", "because", "if", "when", "while", "although",
    "though", "since", "unless", "then", "yet", "or", "nor",
})


@dataclass
class TargetCue:
    start: float
    end: float
    lines: list[str]

    @property
    def text(self) -> str:
        return " ".join(self.lines)


def _is_title_abbreviation_period(text: str, period_pos: int) -> bool:
    """True if the "." at `period_pos` closes a known title abbreviation
    ("Mr.", "Dr.", ...) rather than ending a sentence."""
    word_start = period_pos
    while word_start > 0 and text[word_start - 1].isalpha():
        word_start -= 1
    return text[word_start:period_pos].casefold() in _TITLE_ABBREVIATIONS


def split_sentences(text: str) -> list[str]:
    """Split on sentence-ending punctuation, keeping the punctuation
    attached to the sentence it closes. A "." closing a known title
    abbreviation (see _TITLE_ABBREVIATIONS) is never treated as a
    sentence end."""
    pieces = []
    i = 0
    for m in _SENTENCE_END.finditer(text):
        end = m.end()
        if text[m.start()] == "." and _is_title_abbreviation_period(text, m.start()):
            continue
        piece = text[i:end].strip()
        if piece:
            pieces.append(piece)
        i = end
    tail = text[i:].strip()
    if tail:
        pieces.append(tail)
    return pieces or ([text.strip()] if text.strip() else [])


def _boundary_score(prev_word: str, next_word: str) -> float:
    score = 0.0
    if _SENTENCE_END.search(prev_word + " "):
        score += 4.0
    elif _CLAUSE_END.search(prev_word):
        score += 2.0
    next_bare = next_word.strip(_STRIP_PUNCT).lower()
    if next_bare in _STRONG_CONJUNCTIONS:
        score += 1.0
    prev_bare = prev_word.strip(_STRIP_PUNCT).lower()
    if prev_bare in _CLINGY_WORDS:
        score -= 3.0
    return score


def split_long_piece(piece: str, max_chars: int = MAX_CUE_CHARS) -> list[str]:
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
        score = _boundary_score(words[pos - 1], words[pos]) - abs(pos - len(words) / 2) * 0.05
        if best_score is None or score > best_score:
            best_score, best_pos = score, pos
    if best_pos is None:
        # No split keeps both halves under budget -- minimise the longer
        # half rather than leaving the sentence whole and over-length.
        best_pos = min(range(1, len(words)),
                       key=lambda p: max(len(" ".join(words[:p])), len(" ".join(words[p:]))))
    left, right = " ".join(words[:best_pos]), " ".join(words[best_pos:])
    return split_long_piece(left, max_chars) + split_long_piece(right, max_chars)


def wrap_lines(text: str, max_line_chars: int = MAX_LINE_CHARS) -> list[str]:
    if len(text) <= max_line_chars:
        return [text]
    words = text.split()
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) > max_line_chars or len(b) > max_line_chars:
            continue
        cost = abs(len(a) - len(b)) - _boundary_score(a, b) * 2
        if best is None or cost < best[0]:
            best = (cost, [a, b])
    if best:
        return best[1]
    mid = min(range(1, len(words)),
             key=lambda i: max(len(" ".join(words[:i])), len(" ".join(words[i:]))))
    return [" ".join(words[:mid]), " ".join(words[mid:])]


def _reading_duration(text: str) -> float:
    return max(MIN_DURATION, len(text) / TARGET_CPS)


def distribute_timing(pieces: list[str], envelope_start: float, envelope_end: float) -> list[tuple[float, float]]:
    """Time each piece by its own estimated reading duration (characters
    at TARGET_CPS), then rescale proportionally so the pieces exactly fill
    the source envelope -- reading time sets the *ratio* between pieces;
    the envelope (real source audio timing) sets the absolute scale. No
    piece's duration is ever derived from source word count."""
    envelope = envelope_end - envelope_start
    if not pieces:
        return []
    if len(pieces) == 1:
        return [(envelope_start, envelope_end)]
    wanted = [_reading_duration(p) for p in pieces]
    total_wanted = sum(wanted)
    if total_wanted <= 0:
        total_wanted = 1.0
    scale = envelope / total_wanted
    out = []
    t = envelope_start
    for i, w in enumerate(wanted):
        dur = w * scale
        end = envelope_end if i == len(wanted) - 1 else t + dur
        out.append((t, end))
        t = end
    return out


def extend_short_cues(cues: list, *, min_duration: float = MIN_DURATION,
                      max_duration: float = MAX_DURATION, target_cps: float = TARGET_CPS) -> None:
    """Mutates `.end` in place: a cue too short to read comfortably (a
    genuinely brief source utterance, e.g. a 0.18s "Eda,") gets its
    display time extended toward a comfortable reading duration, capped
    by the next cue's start (never overlaps) and by max_duration. Ported
    from a design the audit found sound in the prior implementation, not
    reused blindly -- confirmed necessary by a real-audio integration run
    that produced several sub-1-second cues with no mechanism to extend
    them; this is that mechanism. Never invents a *start* time or moves a
    cue earlier -- only ever pushes `end` later, and only within already-
    real silence up to the next cue."""
    for i, cue in enumerate(cues):
        wanted = max(min_duration, len(cue.text) / target_cps)
        end = max(cue.end, cue.start + wanted)
        end = min(end, cue.start + max_duration)
        limit = cues[i + 1].start if i + 1 < len(cues) else None
        if limit is not None:
            end = min(end, limit)
        cue.end = max(end, cue.start + 0.001)


ORPHAN_WORD_THRESHOLD = 2   # a fragment this short (word count, not char
                            # count) reads as an orphan, not a complete
                            # sentence standing on its own


def merge_short_pieces(pieces: list[str], max_chars: int = MAX_CUE_CHARS) -> list[str]:
    """Absorb any too-short fragment into a neighbour rather than leaving
    it as its own cue. Real motivating case (5-minute real-audio test):
    glossary.recover_dropped_entities() prepends a bare canonical mention
    ("Eda.") to top up a missing occurrence; without this pass it became
    its own ~0.35s cue immediately before the sentence it belongs with.
    General-purpose, not entity-recovery-specific -- any short leading/
    trailing fragment gets the same treatment.

    Gated on WORD count, not character count -- a real regression from an
    earlier char-only version: "I went home." (12 chars, 3 words) is a
    genuinely complete sentence that must stand alone, while "Eda." (4
    chars, 1 word) is a bare fragment. Word count tells these apart;
    character length alone does not (caught by
    test_multiple_sentences_split_at_sentence_boundaries_not_fragments)."""
    if len(pieces) <= 1:
        return pieces
    merged = list(pieces)
    changed = True
    while changed and len(merged) > 1:
        changed = False
        for i, p in enumerate(merged):
            if len(p.split()) > ORPHAN_WORD_THRESHOLD:
                continue
            if i + 1 < len(merged) and len(p) + 1 + len(merged[i + 1]) <= max_chars:
                merged[i:i + 2] = [f"{p} {merged[i + 1]}"]
                changed = True
                break
            if i - 1 >= 0 and len(merged[i - 1]) + 1 + len(p) <= max_chars:
                merged[i - 1:i + 1] = [f"{merged[i - 1]} {p}"]
                changed = True
                break
    return merged


_DASH_DIALOGUE_LINE = re.compile(r"^-\s*.+$")


def _two_speaker_dialogue_lines(text: str, max_line_chars: int) -> list[str] | None:
    """Detects translate.py's join_multi_speaker_dash_lines() output
    ("- Line one.\\n- Line two.") when both lines individually still fit
    one MAX_LINES-line cue -- so segment() can keep it as ONE simultaneous
    cue for its whole envelope instead of running it through the normal
    per-sentence split below.

    Real evidence (Season 01 full-batch QC, 2026-09-20): the source .srt
    already displays a two-speaker dash cue as ONE 2-line block for its
    WHOLE original duration -- viewers read both lines together. Once
    each line is correctly translated independently (see
    translate.split_multi_speaker_dash_lines()'s docstring), each line
    ends with its own sentence-ending punctuation, so the generic
    split_sentences() path below saw two sentences and split them into
    two SEPARATE sequential cues, each getting only a fraction of the
    original envelope -- turning a single-speaker readability problem
    into a much worse one for a cue that was never meant to be read
    sequentially in the first place. Confirmed the majority contributor
    to that regression: about half of every episode's sub-1-second
    "duration below minimum" findings were dash-prefixed cues.

    Returns None (falls through to the normal pipeline) if a line would
    overflow max_line_chars on its own -- correctness of the 2-line/
    42-char-per-line display constraint always wins over keeping the
    original presentation."""
    lines = text.split("\n")
    if len(lines) != MAX_LINES:
        return None
    if not all(_DASH_DIALOGUE_LINE.match(l) for l in lines):
        return None
    if any(len(l) > max_line_chars for l in lines):
        return None
    return lines


def segment(text: str, envelope_start: float, envelope_end: float, *,
           max_cue_chars: int = MAX_CUE_CHARS, max_line_chars: int = MAX_LINE_CHARS) -> list[TargetCue]:
    """The full pipeline: sentence split -> long-sentence split -> orphan-
    fragment merge -> reading-time-weighted timing -> per-cue line wrap.

    A two-speaker dash-dialogue cue (see _two_speaker_dialogue_lines())
    skips all of that and returns as ONE cue spanning the whole envelope,
    matching how the source .srt itself presented it."""
    dialogue_lines = _two_speaker_dialogue_lines(text, max_line_chars)
    if dialogue_lines is not None:
        return [TargetCue(start=envelope_start, end=envelope_end, lines=dialogue_lines)]
    sentences = split_sentences(text)
    pieces: list[str] = []
    for s in sentences:
        pieces.extend(split_long_piece(s, max_cue_chars))
    pieces = [p for p in pieces if p.strip()] or ([text.strip()] if text.strip() else [])
    pieces = merge_short_pieces(pieces, max_cue_chars)
    timings = distribute_timing(pieces, envelope_start, envelope_end)
    return [TargetCue(start=s, end=e, lines=wrap_lines(p, max_line_chars))
           for p, (s, e) in zip(pieces, timings)]
