"""SRT is an OUTPUT format only -- never read back in as the canonical
representation (see transcript.py). This module renders TargetCue/Segment
lists to SRT text and, separately, parses an SRT file back into plain
cues for QC/reference-comparison purposes only (never fed into the
pipeline as transcription or translation input -- see media.py /
pipeline.py for the audio-first guarantee)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SrtCue:
    start: float
    end: float
    text: str


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def render(cues: list) -> str:
    """Accepts anything with .start/.end and either .lines or .text."""
    blocks = []
    for i, cue in enumerate(cues, 1):
        lines = getattr(cue, "lines", None) or [getattr(cue, "text", "")]
        blocks.append(f"{i}\n{_ts(cue.start)} --> {_ts(cue.end)}\n" + "\n".join(lines) + "\n")
    return "\n".join(blocks)


_STAMP = re.compile(r"(\d+):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2}),(\d{3})")


def parse(path: str | Path) -> list[SrtCue]:
    raw = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    cues = []
    for block in raw.split("\n\n"):
        lines = block.strip("\n").split("\n")
        if len(lines) < 3:
            continue
        m = _STAMP.search(lines[1]) if len(lines) > 1 else None
        if not m:
            continue
        g = list(map(int, m.groups()))
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        cues.append(SrtCue(start, end, " ".join(lines[2:])))
    return cues
