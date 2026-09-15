"""Groups an existing subtitle file's own cues into SourceChunks by pause gap between
consecutive cues' own timestamps -- the direct-translation job type's equivalent of
`stage9_target_segmentation/chunker.py::group_into_source_chunks`, adapted for
`ParsedSrtCue` (a plain cue with its own start/end already given, no ASR word-level
timestamps to derive a span from, and no suppression concept -- every cue in an uploaded
file is real content the uploader wants translated).

Real motivation, not a guess: a real 5-episode comparison (translating the exact same
underlying ASR-produced Turkish text once through this job type and once through the
full audio pipeline) found direct-translation's previous 1:1 cue-to-chunk behavior scored
consistently lower chrF/word-F1 than the audio pipeline's pause-gap-grouped chunks, in
every single episode -- translating one bare, isolated cue at a time starves NLLB of the
surrounding-sentence context that materially improves translation quality. Grouping here
purely for *translation context* is what closes that gap; genuine cue-level re-splitting
of the (possibly reflowed) translated text, if a group's translation doesn't map back
1:1, is Stage 9/10's job (`segment_into_cue_drafts` + `project_timing`), reused as-is by
worker.py — not reimplemented here.
"""
from __future__ import annotations

from app.pipeline.interfaces import SourceChunk
from app.pipeline.stage11_qc_output.formatters.srt import ParsedSrtCue
from app.pipeline.stage9_target_segmentation.chunker import DEFAULT_MAX_CHUNK_CHARS, DEFAULT_MAX_CHUNK_SEGMENTS


def group_cues_into_source_chunks(
    cues: list[ParsedSrtCue], pause_gap_s: float,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS, max_chunk_segments: int = DEFAULT_MAX_CHUNK_SEGMENTS,
) -> list[SourceChunk]:
    if not cues:
        return []

    def cue_chars(c: ParsedSrtCue) -> int:
        return len(c.text.replace("\n", " "))

    chunks: list[SourceChunk] = []
    current = [cues[0]]
    current_chars = cue_chars(cues[0])
    prev_end = cues[0].end

    for cue in cues[1:]:
        gap = cue.start - prev_end
        would_exceed_budget = (
            len(current) >= max_chunk_segments
            or current_chars + 1 + cue_chars(cue) > max_chunk_chars
        )
        if gap >= pause_gap_s or would_exceed_budget:
            chunks.append(_finalize_cue_chunk(current, len(chunks)))
            current = [cue]
            current_chars = cue_chars(cue)
        else:
            current.append(cue)
            current_chars += 1 + cue_chars(cue)
        prev_end = cue.end

    chunks.append(_finalize_cue_chunk(current, len(chunks)))
    return chunks


def _finalize_cue_chunk(cues: list[ParsedSrtCue], index: int) -> SourceChunk:
    text = " ".join(c.text.replace("\n", " ") for c in cues if c.text.strip())
    return SourceChunk(
        chunk_id=f"chunk_{index:05d}", source_text=text,
        word_span=(cues[0].start, cues[-1].end),
        source_segment_ids=[str(c.index) for c in cues],
    )
