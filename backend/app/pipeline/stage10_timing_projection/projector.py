"""Stage 10: Timing Projection.

Derives every target cue's start/end strictly from the source chunk's real word-timestamp
span (`SourceChunk.word_span`, itself built only from ASR word timestamps) — never a
global time shift, never Subsync-style resync against an external reference. A cue that
is one *piece* of a split chunk (Stage 9's N:M split direction) gets a proportional slice
of that chunk's duration sized by its character share; a cue that *merges* multiple
chunks spans the union of those chunks' timestamps.

The only adjustment made here is a minimum-duration floor for readability, and even that
is clamped so it can never push a cue past the next cue's start (i.e. never invents an
overlap) — if the available gap is too tight to reach the floor, the cue is left short and
Stage 11 QC is what surfaces that as a finding, rather than this stage silently fabricating
time that doesn't exist in the source.
"""
from __future__ import annotations

from app.pipeline.interfaces import SourceChunk, TargetCue, TargetCueDraft

_OVERLAP_EPSILON = 0.001


def project_timing(
    drafts: list[TargetCueDraft], chunks: list[SourceChunk], min_cue_duration_s: float,
) -> list[TargetCue]:
    chunks_by_id = {c.chunk_id: c for c in chunks}
    cumulative_offset: dict[str, float] = {}

    raw_cues: list[TargetCue] = []
    for draft in drafts:
        if len(draft.source_chunk_ids) == 1:
            chunk_id = draft.source_chunk_ids[0]
            chunk = chunks_by_id[chunk_id]
            share = draft.char_share_of_chunks[chunk_id]
            chunk_start, chunk_end = chunk.word_span
            chunk_duration = chunk_end - chunk_start

            offset = cumulative_offset.get(chunk_id, 0.0)
            start = chunk_start + offset
            piece_duration = chunk_duration * share
            end = start + piece_duration
            cumulative_offset[chunk_id] = offset + piece_duration

            primary_share = share
        else:
            spans = [chunks_by_id[cid].word_span for cid in draft.source_chunk_ids]
            start = min(s[0] for s in spans)
            end = max(s[1] for s in spans)
            primary_share = draft.char_share_of_chunks[draft.source_chunk_ids[0]]

        raw_cues.append(TargetCue(
            cue_id=draft.draft_id, start=start, end=end, text=draft.text,
            source_chunk_ids=draft.source_chunk_ids, char_share_of_chunk=primary_share,
        ))

    return _enforce_minimum_duration(raw_cues, min_cue_duration_s)


def _enforce_minimum_duration(cues: list[TargetCue], min_cue_duration_s: float) -> list[TargetCue]:
    adjusted: list[TargetCue] = []
    for i, cue in enumerate(cues):
        duration = cue.end - cue.start
        if duration >= min_cue_duration_s:
            adjusted.append(cue)
            continue

        desired_end = cue.start + min_cue_duration_s
        next_start = cues[i + 1].start if i + 1 < len(cues) else None
        if next_start is not None and desired_end > next_start:
            desired_end = max(cue.end, next_start - _OVERLAP_EPSILON)

        adjusted.append(TargetCue(
            cue_id=cue.cue_id, start=cue.start, end=desired_end, text=cue.text,
            source_chunk_ids=cue.source_chunk_ids, char_share_of_chunk=cue.char_share_of_chunk,
        ))
    return adjusted
