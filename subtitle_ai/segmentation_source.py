"""Source-language cue segmentation: groups a flat word stream (already
hallucination-checked and normalized) into display-oriented cues, deciding
*why* each boundary exists at the point where the acoustic evidence
actually is -- word gaps and punctuation -- so nothing downstream ever
has to re-guess it from rendered SRT timestamps.

This is a clean reimplementation of an idea the audit found genuinely
sound (the prior system's boundary_before design worked); the *docstring
reasoning* is retained because it's correct, not because the old code is
authoritative -- see transcript.BoundaryReason.
"""

from __future__ import annotations

import re

from transcript import BoundaryReason, Segment, Word

MAX_CUE_CHARS = 84            # ~2 lines x 42 chars
MAX_DURATION = 7.0
MAX_GAP = 0.8                 # silence between words that forces a break

_SENTENCE_END = re.compile(r"[.!?…]['\"»)\]]*$")


def build_cues(words: list[Word]) -> list[Segment]:
    """Flatten -> regroup into display cues. Suppressed (hallucinated)
    words are dropped entirely before grouping -- a suppressed word must
    never anchor or extend a cue boundary."""
    live = [w for w in words if w.text.strip()]
    cues: list[Segment] = []
    cur: list[Word] = []
    pending_reason: BoundaryReason | None = None

    def flush(next_reason: BoundaryReason | None) -> None:
        nonlocal pending_reason
        if not cur:
            return
        cues.append(Segment(
            index=len(cues), start=cur[0].start, end=cur[-1].end, words=list(cur),
            avg_logprob=0.0, no_speech_prob=0.0, compression_ratio=0.0,
            boundary_before=pending_reason))
        pending_reason = next_reason

    for w in live:
        if not cur:
            cur = [w]
            continue
        cur_text = " ".join(x.text for x in cur)
        gap = w.start - cur[-1].end
        dur = w.end - cur[0].start
        too_long = len(cur_text) + 1 + len(w.text) > MAX_CUE_CHARS
        too_slow = dur > MAX_DURATION
        sentence_end = bool(_SENTENCE_END.search(cur_text)) and len(cur_text) >= 12

        if gap > MAX_GAP or too_slow or too_long or sentence_end:
            if gap > MAX_GAP:
                reason = BoundaryReason.REAL_ACOUSTIC_GAP
            elif too_slow:
                reason = BoundaryReason.MAX_DURATION
            elif too_long:
                reason = BoundaryReason.MAX_LENGTH
            else:
                reason = BoundaryReason.SENTENCE_END
            flush(reason)
            cur = [w]
        else:
            cur.append(w)
    flush(None)
    return cues
