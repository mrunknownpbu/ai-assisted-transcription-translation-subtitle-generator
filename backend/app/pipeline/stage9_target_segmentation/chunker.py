"""Groups canonical (post-normalization, post-hallucination-defense) segments into
SourceChunks by pause gap in the actual word timestamps — not by guessing sentence
boundaries from text alone. A chunk break happens wherever the gap between one kept
segment's last word and the next kept segment's first word is >= `pause_gap_s`; anything
suppressed by Stage 6 is skipped entirely; it never reaches translation or output.

Chunking (rather than translating single short ASR segments) gives the translation
engine a full breath-group of context, which is what makes natural, syntax-aware target
segmentation possible in Stage 9's re-cut and honest N:M mapping in the output.

A chunk also breaks on `max_chunk_chars`/`max_chunk_segments`, independent of pause gap —
without this cap, a long dialogue run with only short pauses could grow one SourceChunk
past the translation engine's practical input length, silently losing the overflow to
tokenizer truncation instead of ever reaching the translator. Capping here, before
translation, is what prevents that.
"""
from __future__ import annotations

from app.pipeline.interfaces import CanonicalTranscript, SourceChunk

DEFAULT_MAX_CHUNK_CHARS = 300
DEFAULT_MAX_CHUNK_SEGMENTS = 6


def group_into_source_chunks(
    transcript: CanonicalTranscript, pause_gap_s: float,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS, max_chunk_segments: int = DEFAULT_MAX_CHUNK_SEGMENTS,
) -> list[SourceChunk]:
    kept_segments = [s for s in transcript.segments if not s.suppressed]
    if not kept_segments:
        return []

    chunks: list[SourceChunk] = []
    current_segments = [kept_segments[0]]
    current_chars = len(kept_segments[0].normalized_text)
    prev_end = kept_segments[0].end

    for seg in kept_segments[1:]:
        gap = seg.start - prev_end
        seg_chars = len(seg.normalized_text)
        would_exceed_budget = (
            len(current_segments) >= max_chunk_segments
            or current_chars + 1 + seg_chars > max_chunk_chars
        )
        if gap >= pause_gap_s or would_exceed_budget:
            chunks.append(_finalize_chunk(current_segments, len(chunks)))
            current_segments = [seg]
            current_chars = seg_chars
        else:
            current_segments.append(seg)
            current_chars += 1 + seg_chars
        prev_end = seg.end

    chunks.append(_finalize_chunk(current_segments, len(chunks)))
    return chunks


def _finalize_chunk(segments, index: int) -> SourceChunk:
    text = " ".join(s.normalized_text for s in segments if s.normalized_text.strip())
    all_words = [w for s in segments for w in s.words]
    if all_words:
        span = (all_words[0].start, all_words[-1].end)
    else:
        span = (segments[0].start, segments[-1].end)
    return SourceChunk(
        chunk_id=f"chunk_{index:05d}", source_text=text, word_span=span,
        source_segment_ids=[s.segment_id for s in segments],
    )
