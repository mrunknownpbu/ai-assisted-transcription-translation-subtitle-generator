"""Source-language cue segmentation: groups a flat word stream (already
hallucination-checked and normalized) into display-oriented cues, deciding
*why* each boundary exists at the point where the acoustic evidence
actually is -- word gaps and punctuation -- so nothing downstream ever
has to re-guess it from rendered SRT timestamps.

This is a clean reimplementation of an idea the audit found genuinely
sound (the prior system's boundary_before design worked); the *docstring
reasoning* is retained because it's correct, not because the old code is
authoritative -- see transcript.BoundaryReason.

Two passes, kept deliberately separate (2026-09-28 natural-dialogue
rework -- see benchmark-results/segmentation-naturalness-baseline-*.json
for the "before" numbers this replaced):

1. ACOUSTIC grouping only -- a real silence gap (MAX_GAP) or an utterance
   running past MAX_DURATION forces a break. Nothing about sentence shape
   enters here, so a group's extent always reflects when someone actually
   paused, never how long the resulting text happens to be.
2. Each acoustic group is split at SENTENCE boundaries -- every complete
   sentence becomes its own cue, with NO minimum length: a short "Tamam."
   right before a pause is a real, complete cue, not glued onto whatever
   follows (the old MAX_CUE_CHARS/12-char-gate version did that gluing,
   and never wrapped lines or split at a clause boundary -- see
   ENHANCEMENT_DRAFT.md/the natural-dialogue plan for the human-subtitle
   comparison that motivated this). A sentence still too long for one cue
   is split at the best-scoring clause/conjunction boundary near its
   midpoint (text_segmentation.py, ported from segmentation_target.py's
   already-validated algorithm) instead of an arbitrary character cutoff,
   and each resulting cue's `.lines` is wrapped to 2x42 chars.
"""

from __future__ import annotations

import re

from text_segmentation import ENGLISH, TURKISH, SplitLexicon, _boundary_score, wrap_lines
from transcript import NO_SPACE_LANGUAGES, BoundaryReason, Segment, Word, render_words

MAX_CUE_CHARS = 84            # ~2 lines x 42 chars
MAX_LINE_CHARS = 42
MAX_DURATION = 7.0
MAX_GAP = 0.8                 # silence between words that forces a break

_SENTENCE_END = re.compile(r"[.!?…]['\"»)\]]*$")

# Per-language tie-break word lists for the clause-boundary search (see
# text_segmentation.SplitLexicon) -- unknown languages get an empty
# lexicon (punctuation-only splitting) rather than silently borrowing
# English's or Turkish's word lists for the wrong language.
_LEXICONS: dict[str, SplitLexicon] = {"tr": TURKISH, "en": ENGLISH}


def _lexicon_for(language: str) -> SplitLexicon:
    return _LEXICONS.get(language, SplitLexicon())


def _is_title_abbrev(word_text: str, lexicon: SplitLexicon) -> bool:
    """word-level check mirroring text_segmentation's text-level one: ASR
    emits punctuation attached to its word ("Dr." as one token), so this
    strips the trailing period and checks the bare form."""
    return word_text.endswith(".") and word_text[:-1].casefold() in lexicon.title_abbreviations


def _sentence_end_word(word_text: str, lexicon: SplitLexicon) -> bool:
    return bool(_SENTENCE_END.search(word_text)) and not _is_title_abbrev(word_text, lexicon)


def _split_long_words(ws: list[Word], language: str, lexicon: SplitLexicon,
                      max_chars: int = MAX_CUE_CHARS) -> list[list[Word]]:
    """Word-level port of text_segmentation.split_long_piece: the same
    best-scoring-boundary search, operating on Word spans (via
    render_words for text/length) instead of plain text -- so the
    resulting pieces map back onto real word/timestamp ranges without a
    lossy text<->word realignment step."""
    if len(ws) < 2 or len(render_words(ws, language)) <= max_chars:
        return [ws]
    best_pos, best_score = None, None
    for pos in range(1, len(ws)):
        left = render_words(ws[:pos], language)
        right = render_words(ws[pos:], language)
        if len(left) > max_chars or len(right) > max_chars:
            continue
        score = _boundary_score(ws[pos - 1].text, ws[pos].text, lexicon) - abs(pos - len(ws) / 2) * 0.05
        if best_score is None or score > best_score:
            best_score, best_pos = score, pos
    if best_pos is None:
        # No split keeps both halves under budget -- minimise the longer
        # half rather than leaving the piece whole and over-length.
        best_pos = min(range(1, len(ws)),
                       key=lambda p: max(len(render_words(ws[:p], language)), len(render_words(ws[p:], language))))
    return (_split_long_words(ws[:best_pos], language, lexicon, max_chars)
           + _split_long_words(ws[best_pos:], language, lexicon, max_chars))


def build_cues(words: list[Word], language: str = "") -> list[Segment]:
    """Flatten -> regroup into display cues. Suppressed (hallucinated)
    words are dropped entirely before grouping -- a suppressed word must
    never anchor or extend a cue boundary.

    `language` (see transcript.NO_SPACE_LANGUAGES) affects Segment.text
    rendering, the length estimates here, and which SplitLexicon (see
    _lexicon_for) tie-breaks a clause split -- for unspaced languages, no
    line-wrap is attempted (word-count-based wrapping is meaningless
    without word boundaries), matching this project's existing NO_SPACE_LANGUAGES
    handling elsewhere."""
    live = [w for w in words if w.text.strip()]
    lexicon = _lexicon_for(language)
    unspaced = language in NO_SPACE_LANGUAGES

    # Pass 1: acoustic-only groups.
    groups: list[tuple[list[Word], BoundaryReason | None]] = []
    cur: list[Word] = []
    pending_reason: BoundaryReason | None = None
    for w in live:
        if not cur:
            cur = [w]
            continue
        gap = w.start - cur[-1].end
        dur = w.end - cur[0].start
        if gap > MAX_GAP or dur > MAX_DURATION:
            groups.append((cur, pending_reason))
            pending_reason = BoundaryReason.REAL_ACOUSTIC_GAP if gap > MAX_GAP else BoundaryReason.MAX_DURATION
            cur = [w]
        else:
            cur.append(w)
    if cur:
        groups.append((cur, pending_reason))

    # Pass 2: sentence split, then clause split for anything still too
    # long, then line-wrap -- within each acoustic group.
    cues: list[Segment] = []
    for ws, group_reason in groups:
        sentences: list[list[Word]] = []
        piece: list[Word] = []
        for w in ws:
            piece.append(w)
            if _sentence_end_word(w.text, lexicon):
                sentences.append(piece)
                piece = []
        if piece:
            sentences.append(piece)

        for si, sentence in enumerate(sentences):
            sentence_reason = group_reason if si == 0 else BoundaryReason.SENTENCE_END
            sub_pieces = _split_long_words(sentence, language, lexicon, MAX_CUE_CHARS)
            for pi, sub in enumerate(sub_pieces):
                reason = sentence_reason if pi == 0 else BoundaryReason.DISPLAY_SPLIT
                text = render_words(sub, language)
                lines = [text] if unspaced else wrap_lines(text, MAX_LINE_CHARS, lexicon)
                cues.append(Segment(
                    index=len(cues), start=sub[0].start, end=sub[-1].end, words=list(sub),
                    avg_logprob=0.0, no_speech_prob=0.0, compression_ratio=0.0,
                    boundary_before=reason, language=language, lines=lines))
    return cues
