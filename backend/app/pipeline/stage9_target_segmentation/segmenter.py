"""Re-cuts *translated* chunk text into subtitle-appropriate cues — genuine N:M mapping
against the source: a long translated chunk splits into several cues (1 source chunk ->
N cues), and several short, temporally-adjacent chunks merge into a single cue (M source
chunks -> 1 cue), rather than forcing one cue per source segment.

Only cue *text* and each cue's proportional share of its source chunk(s) are decided
here — actual start/end timestamps are Stage 10's job, since duration budgeting (reading
speed, minimum duration) can only be computed once real timing is projected.
"""
from __future__ import annotations

import re

from app.pipeline.interfaces import TargetCueDraft, TranslatedChunk

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def wrap_text(text: str, max_chars_per_line: int, max_lines_per_cue: int) -> str:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars_per_line or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
        if len(lines) == max_lines_per_cue:
            break
    if current and len(lines) < max_lines_per_cue:
        lines.append(current)
    # Any words left over after filling max_lines just get appended to the last line —
    # QC (Stage 11) is what flags a cue as over-length/over-CPS, not silent truncation
    # here, since dropping words would violate "capture all dialogue".
    consumed = sum(len(l.split()) for l in lines)
    leftover = words[consumed:]
    if leftover and lines:
        lines[-1] = (lines[-1] + " " + " ".join(leftover)).strip()
    return "\n".join(lines)


def _greedy_pack(units: list[str], budget: int, min_trailing_chars: int) -> list[str]:
    """Greedily packs `units` (sentences or words) into pieces of at most `budget` chars.

    Naive greedy packing can leave a tiny final piece -- e.g. the very last word of a long
    sentence, once everything before it has filled the previous piece to just under budget.
    Stage 10 allocates each piece a slice of the source chunk's *real spoken duration*
    proportional to its *character length*, so a lone trailing word gets an almost-zero
    share, floored up to the minimum-cue-duration -- a genuine bug this reproduced in
    production: a 45-minute episode's translation isolated the word "day." from the
    sentence it belonged to into its own 0.8-second cue. Merging a too-small trailing piece
    back into the one before it (even past `budget`) avoids that; QC (Stage 11) is what
    flags a cue as over-length/over-CPS, not silent truncation here, matching how
    `wrap_text`'s own leftover-word handling already treats this same tradeoff.
    """
    pieces: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current} {unit}".strip()
        if len(candidate) <= budget or not current:
            current = candidate
        else:
            pieces.append(current)
            current = unit
    if current:
        pieces.append(current)

    if len(pieces) >= 2 and len(pieces[-1]) < min_trailing_chars:
        pieces[-2] = f"{pieces[-2]} {pieces[-1]}".strip()
        pieces.pop()
    return pieces


def _split_long_chunk(chunk: TranslatedChunk, budget: int, max_chars_per_line: int, max_lines_per_cue: int) -> list[TargetCueDraft]:
    text = chunk.translated_text.strip()
    if len(text) <= budget:
        return [TargetCueDraft(
            draft_id=f"{chunk.chunk_id}_d0", text=wrap_text(text, max_chars_per_line, max_lines_per_cue),
            source_chunk_ids=[chunk.chunk_id], char_share_of_chunks={chunk.chunk_id: 1.0},
        )]

    # NOTE: a chunk merging more than one original ASR segment (chunker.py does this
    # deliberately, for translation context) can have its clauses reordered by translation
    # -- routine between e.g. Chinese and English -- which means a split piece's character
    # position no longer reliably matches *when* that content was actually spoken. One real
    # production case (found via the translation-accuracy evaluation harness) mistimed a
    # trailing fragment this way. An earlier fix refused to split such chunks at all,
    # keeping them as one cue over the correct full span -- but on a real 39-episode-scale
    # job that produced far more damage than it prevented: 442 QC line-length/reading-speed
    # failures on one single episode (every long multi-segment chunk became one massive,
    # unreadable cue), against the one narrow case it was written for. Splitting normally
    # (below) is the better empirical tradeoff: a split piece can be timed a few seconds off
    # within its own chunk's span in the rare reordering case, which is far less damaging
    # than an unreadable, QC-failing wall of text on nearly every long chunk.

    # A trailing piece under ~15% of budget is disproportionately small relative to the
    # real audio-duration slice Stage 10 will give it from its character share alone.
    min_trailing_chars = max(12, int(budget * 0.15))

    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()] or [text]
    pieces = _greedy_pack(sentences, budget, min_trailing_chars)

    # A single sentence longer than the whole budget still needs splitting by words so no
    # cue is left absurdly long.
    final_pieces: list[str] = []
    for piece in pieces:
        if len(piece) <= budget:
            final_pieces.append(piece)
            continue
        final_pieces.extend(_greedy_pack(piece.split(), budget, min_trailing_chars))

    total_len = sum(len(p) for p in final_pieces) or 1
    drafts = []
    for i, piece in enumerate(final_pieces):
        share = len(piece) / total_len
        drafts.append(TargetCueDraft(
            draft_id=f"{chunk.chunk_id}_d{i}", text=wrap_text(piece, max_chars_per_line, max_lines_per_cue),
            source_chunk_ids=[chunk.chunk_id], char_share_of_chunks={chunk.chunk_id: share},
        ))
    return drafts


def segment_into_cue_drafts(
    chunks: list[TranslatedChunk], max_chars_per_line: int, max_lines_per_cue: int,
    max_merge_gap_s: float = 1.0,
) -> list[TargetCueDraft]:
    budget = max_chars_per_line * max_lines_per_cue
    drafts: list[TargetCueDraft] = []
    i = 0
    n = len(chunks)

    while i < n:
        chunk = chunks[i]
        text = chunk.translated_text.strip()

        if not text:
            i += 1
            continue

        merge_threshold = budget * 0.5
        # Two short adjacent chunks merging into one cue must also be close in TIME, not
        # just short in TEXT -- otherwise two chunks either side of a real silence (a song
        # ending, then a fansub-credit watermark 46 seconds later, in one real production
        # case) get merged into a single cue spanning the whole gap between them, via
        # Stage 10's union-of-spans timing for multi-chunk drafts. A real pause this size
        # is exactly what chunker.py's own pause_gap_s already treats as a semantic break
        # before translation ever happens; the same threshold applies here.
        gap = chunks[i + 1].word_span[0] - chunk.word_span[1] if i + 1 < n else None
        if (
            len(text) <= merge_threshold and i + 1 < n and chunks[i + 1].translated_text.strip()
            and gap is not None and gap < max_merge_gap_s
        ):
            next_chunk = chunks[i + 1]
            combined = f"{text} {next_chunk.translated_text.strip()}"
            if len(combined) <= budget:
                drafts.append(TargetCueDraft(
                    draft_id=f"{chunk.chunk_id}_{next_chunk.chunk_id}_merged",
                    text=wrap_text(combined, max_chars_per_line, max_lines_per_cue),
                    source_chunk_ids=[chunk.chunk_id, next_chunk.chunk_id],
                    char_share_of_chunks={chunk.chunk_id: 1.0, next_chunk.chunk_id: 1.0},
                ))
                i += 2
                continue

        drafts.extend(_split_long_chunk(chunk, budget, max_chars_per_line, max_lines_per_cue))
        i += 1

    return drafts
