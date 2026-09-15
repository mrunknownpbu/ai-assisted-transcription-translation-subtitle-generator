from __future__ import annotations

import re
from dataclasses import dataclass

from app.pipeline.interfaces import TargetCue


def _format_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_ms = round(seconds * 1000)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, ms = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def render_srt(cues: list[TargetCue]) -> str:
    """SRT has no comment/metadata syntax, so provenance for this format travels as a
    sidecar JSON file (see `provenance.write_sidecar`) rather than inline."""
    blocks = []
    for i, cue in enumerate(cues, start=1):
        blocks.append(f"{i}\n{_format_timestamp(cue.start)} --> {_format_timestamp(cue.end)}\n{cue.text}\n")
    return "\n".join(blocks)


@dataclass(frozen=True)
class ParsedSrtCue:
    index: int
    start: float
    end: float
    text: str


_TIMESTAMP_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")
_CUE_BLOCK_RE = re.compile(
    r"(\d+)\s*\n(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3}).*?\n(.*?)(?=\n\s*\n\d+\s*\n|\Z)",
    re.DOTALL,
)


def _parse_timestamp(raw: str) -> float:
    match = _TIMESTAMP_RE.match(raw)
    if not match:
        raise ValueError(f"Malformed SRT timestamp: {raw!r}")
    hours, minutes, seconds, millis = (int(g) for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def parse_srt(content: str) -> list[ParsedSrtCue]:
    """Used both for Stage 11's final output-file re-validation (re-parsing the file this
    platform just wrote, independent of the renderer, as a last QC gate) and for the
    direct-subtitle-translation job type's input parsing.

    Handles a leading UTF-8 BOM (`utf-8-sig` encoding upstream) and both `,`/`.` decimal
    separators in timestamps, since real-world SRT files are inconsistent about the latter.
    """
    normalized = content.replace("\r\n", "\n").strip("﻿")
    cues: list[ParsedSrtCue] = []
    for match in _CUE_BLOCK_RE.finditer(normalized + "\n\n"):
        index_str, start_str, end_str, text = match.groups()
        cues.append(ParsedSrtCue(
            index=int(index_str), start=_parse_timestamp(start_str), end=_parse_timestamp(end_str),
            text=text.strip("\n"),
        ))
    return cues
