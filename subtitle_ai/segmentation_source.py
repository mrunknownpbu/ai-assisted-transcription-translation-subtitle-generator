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
from subtitle_constraints import MAX_CHARS_PER_LINE, MAX_CUE_DURATION, MAX_LINES, MIN_CUE_DURATION

MAX_CUE_CHARS = MAX_CHARS_PER_LINE * MAX_LINES
MAX_JAPANESE_CUE_CHARS = 42
MAX_LINE_CHARS = MAX_CHARS_PER_LINE
MAX_DURATION = MAX_CUE_DURATION
MAX_GAP = 0.8                 # silence between words that forces a break
# A short complete utterance can be joined across this small gap, but not
# across a meaningful pause.  Larger gaps remain an acoustic hard boundary.
SHORT_CUE_MERGE_GAP = 0.35

_SENTENCE_END = re.compile(r"[.!?…。？！]['\"»)\]」』）】]*$")

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


def _merge_short_cues(cues: list[Segment], language: str) -> list[Segment]:
    """Contextually absorb display fragments without crossing real turns.

    This is intentionally not a duration-only operation: a candidate must be
    adjacent in the same acoustic run, fit the display budget, stay within the
    maximum duration, and have no meaningful inter-word pause.  The words and
    their timestamps are retained verbatim.
    """
    merged = list(cues)
    changed = True
    while changed and len(merged) > 1:
        changed = False
        for i, cue in enumerate(merged):
            if cue.end - cue.start >= MIN_CUE_DURATION:
                continue
            # A complete short utterance is meaningful subtitle content in
            # its own right.  Preserve it; this pass repairs fragments such
            # as a dangling conjunction or a decoder split mid-phrase.
            if _SENTENCE_END.search(cue.words[-1].text):
                continue
            candidates = ([i + 1] if i + 1 < len(merged) else []) + ([i - 1] if i else [])
            for other_i in candidates:
                left_i, right_i = sorted((i, other_i))
                left, right = merged[left_i], merged[right_i]
                # Speaker turns and acoustic pauses are hard boundaries.
                if right.boundary_before in {BoundaryReason.REAL_ACOUSTIC_GAP,
                                             BoundaryReason.UTTERANCE_END}:
                    continue
                if right.words[0].start - left.words[-1].end > SHORT_CUE_MERGE_GAP:
                    continue
                words = left.words + right.words
                if (words[-1].end - words[0].start > MAX_DURATION or
                        len(render_words(words, language)) > MAX_CUE_CHARS):
                    continue
                joined = Segment(index=left.index, start=words[0].start, end=words[-1].end,
                                 words=words, avg_logprob=min(left.avg_logprob, right.avg_logprob),
                                 no_speech_prob=max(left.no_speech_prob, right.no_speech_prob),
                                 compression_ratio=max(left.compression_ratio, right.compression_ratio),
                                 boundary_before=left.boundary_before, language=language)
                joined.lines = wrap_lines(joined.text, MAX_LINE_CHARS)
                merged[left_i:right_i + 1] = [joined]
                changed = True
                break
            if changed:
                break
    return merged


def build_cues(words: list[Word], language: str = "",
               turn_word_ids: frozenset[int] | None = None) -> list[Segment]:
    """Flatten -> regroup into display cues. Suppressed (hallucinated)
    words are dropped entirely before grouping -- a suppressed word must
    never anchor or extend a cue boundary.

    `language` (see transcript.NO_SPACE_LANGUAGES) affects Segment.text
    rendering, the length estimates here, and which SplitLexicon (see
    _lexicon_for) tie-breaks a clause split -- for unspaced languages, no
    line-wrap is attempted (word-count-based wrapping is meaningless
    without word boundaries), matching this project's existing NO_SPACE_LANGUAGES
    handling elsewhere.

    `turn_word_ids`: `{id(word), ...}` for each word turns.detect_turns()
    marked as a new speaker's first word (SUBTITLE_AI_TURN_DETECTION,
    off by default -- see turns.py/CLAUDE.md). Identity-based, not
    index-based, so it survives this function's own filtering unchanged.
    Both detectors only ever propose a turn right after a complete
    sentence (see turns.py), so this only ever forces an ACOUSTIC-group
    break that pass 2 would very likely have cut into its own cue anyway
    -- the only real effect is the boundary reason recorded there:
    UTTERANCE_END instead of SENTENCE_END, which
    translate.build_context_spans() treats as a real translation-context
    break (each speaker's own sentence), where SENTENCE_END does not."""
    live = [w for w in words if w.text.strip()]
    lexicon = _lexicon_for(language)
    unspaced = language in NO_SPACE_LANGUAGES
    max_cue_chars = MAX_JAPANESE_CUE_CHARS if language == "ja" else MAX_CUE_CHARS

    # Pass 1: acoustic-only groups (plus any forced speaker-turn breaks).
    groups: list[tuple[list[Word], BoundaryReason | None]] = []
    cur: list[Word] = []
    pending_reason: BoundaryReason | None = None
    for w in live:
        if not cur:
            cur = [w]
            continue
        gap = w.start - cur[-1].end
        dur = w.end - cur[0].start
        is_turn = turn_word_ids is not None and id(w) in turn_word_ids
        if gap > MAX_GAP or dur > MAX_DURATION:
            groups.append((cur, pending_reason))
            pending_reason = BoundaryReason.REAL_ACOUSTIC_GAP if gap > MAX_GAP else BoundaryReason.MAX_DURATION
            cur = [w]
        elif is_turn:
            groups.append((cur, pending_reason))
            pending_reason = BoundaryReason.UTTERANCE_END
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
            sub_pieces = _split_long_words(sentence, language, lexicon, max_cue_chars)
            for pi, sub in enumerate(sub_pieces):
                reason = sentence_reason if pi == 0 else BoundaryReason.DISPLAY_SPLIT
                text = render_words(sub, language)
                lines = [text] if unspaced else wrap_lines(text, MAX_LINE_CHARS, lexicon)
                cues.append(Segment(
                    index=len(cues), start=sub[0].start, end=sub[-1].end, words=list(sub),
                    avg_logprob=0.0, no_speech_prob=0.0, compression_ratio=0.0,
                    boundary_before=reason, language=language, lines=lines))
    return _merge_short_cues(cues, language)
