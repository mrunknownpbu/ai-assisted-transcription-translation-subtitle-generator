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


def _parse_blocks(path: str | Path):
    """Shared by parse()/parse_lines() -- yields (start, end, lines) per
    SRT block, lines exactly as they appeared on disk (never joined).
    parse() joins them into one string (its long-standing contract, relied
    on by srt_translation.py/auto_glossary.py/qc/output_qc.py); parse_lines()
    keeps them separate, for anything that needs to preserve/edit the
    original 2-line display structure (see its own docstring)."""
    raw = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
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
        yield start, end, lines[2:]


def parse(path: str | Path) -> list[SrtCue]:
    return [SrtCue(start, end, " ".join(lines)) for start, end, lines in _parse_blocks(path)]


@dataclass
class SrtCueLines:
    start: float
    end: float
    lines: list[str]


def parse_lines(path: str | Path) -> list[SrtCueLines]:
    """Like parse(), but keeps each cue's original display lines separate
    instead of joining them into one string. parse()'s flattening is fine
    for its existing QC/reference-comparison callers (content-only
    comparisons that don't care about line breaks), but is LOSSY for
    anything that re-renders the result: render() already accepts a
    `.lines` list and preserves it (see its own docstring) -- round-
    tripping through plain parse() -> render() would silently collapse
    every multi-line cue in the file onto one line. Real motivation
    (IMPROVEMENT_PLAN.md 4.2, the inline subtitle editor): editing ONE
    cue's text must never reformat every OTHER cue in the same file."""
    return [SrtCueLines(start, end, lines) for start, end, lines in _parse_blocks(path)]
