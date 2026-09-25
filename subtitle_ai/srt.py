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


# ---------------------------------------------------------------------------
# WebVTT input. Streaming-service releases (e.g. DMM-TV WEB-DL) ship their
# subtitle as WebVTT but named ".srt", so the SRT-translation workflow has to
# read it. Converted to plain SRT text up front; nothing downstream ever sees
# VTT, and the pipeline's own srt.render() then writes a real SRT sidecar.
# ---------------------------------------------------------------------------

_VTT_TIMING = re.compile(
    r"^\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})\s*-->\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})(?:\s+.*)?$")
_VTT_TAG = re.compile(r"<[^>]*>")
_VTT_NON_CUE_BLOCKS = ("NOTE", "STYLE", "REGION")


def is_webvtt(text: str) -> bool:
    """True if `text` (BOM already stripped) starts with the WEBVTT signature."""
    return text.startswith("WEBVTT") and (len(text) == 6 or text[6] in " \t\n\r")


def _vtt_stamp_to_srt(stamp: str) -> str:
    """'12.333' / '01:12.333' / '1:02:03.004' -> 'HH:MM:SS,mmm'."""
    clock, _, ms = stamp.replace(",", ".").partition(".")
    parts = [int(p) for p in clock.split(":")]
    while len(parts) < 3:  # WebVTT allows MM:SS.mmm (no hour)
        parts.insert(0, 0)
    h, m, s = parts
    return f"{h:02d}:{m:02d}:{s:02d},{ms}"


def webvtt_to_srt(text: str) -> str:
    """Convert WebVTT text to SRT text (LF line endings, cues renumbered 1..N).

    Dropped, because SRT has no equivalent and they are not dialogue: the
    header, NOTE/STYLE/REGION blocks, cue identifiers, cue settings after the
    timing (`align:start position:0%`) and inline markup (`<c.x>`, `<v Name>`,
    `<i>`, karaoke `<00:00:01.000>` stamps). HTML entities are decoded.
    Raises ValueError on a cue block that has no timing line, matching the
    strictness of the SRT reader this feeds (never silently truncate)."""
    import html

    blocks = re.split(r"\n{2,}", text.replace("\r\n", "\n").replace("\r", "\n").strip("\n"))
    out: list[str] = []
    number = 0
    for i, block in enumerate(blocks):
        lines = block.split("\n")
        if i == 0 and is_webvtt(block):
            continue  # the header block
        if not any(line.strip() for line in lines):
            continue
        if lines[0].startswith(_VTT_NON_CUE_BLOCKS) and "-->" not in lines[0]:
            continue
        # An optional cue identifier line may precede the timing line.
        timing_at = next((k for k, line in enumerate(lines[:2]) if "-->" in line), None)
        if timing_at is None:
            raise ValueError(f"malformed WebVTT cue block (missing timing line): {block!r}")
        m = _VTT_TIMING.match(lines[timing_at])
        if not m:
            raise ValueError(f"malformed WebVTT timing line: {lines[timing_at]!r}")
        body = [html.unescape(_VTT_TAG.sub("", line)).replace("\xa0", " ").strip()
                for line in lines[timing_at + 1:]]
        number += 1
        out.append("\n".join([str(number),
                              f"{_vtt_stamp_to_srt(m.group(1))} --> {_vtt_stamp_to_srt(m.group(2))}",
                              *body]))
    return "\n\n".join(out) + "\n"


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
