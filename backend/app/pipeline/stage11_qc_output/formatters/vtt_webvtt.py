"""WebVTT and .vtt are the same format; both file extensions are produced from this one
renderer because the spec calls for both explicitly as separate downloadable deliverables.
Unlike SRT, WebVTT has a NOTE block, so provenance travels inline here as well as in the
sidecar JSON every format gets.
"""
from __future__ import annotations

from app.pipeline.interfaces import TargetCue


def _format_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_ms = round(seconds * 1000)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, ms = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def render_webvtt(cues: list[TargetCue], provenance: dict | None = None) -> str:
    lines = ["WEBVTT", ""]

    if provenance:
        lines.append("NOTE")
        for key, value in provenance.items():
            lines.append(f"{key}: {value}")
        lines.append("")

    for i, cue in enumerate(cues, start=1):
        lines.append(str(i))
        lines.append(f"{_format_timestamp(cue.start)} --> {_format_timestamp(cue.end)}")
        lines.append(cue.text)
        lines.append("")

    return "\n".join(lines)
