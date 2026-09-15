"""Converts a job's canonical transcript into ParsedSrtCue-shaped hypothesis cues for the
evaluation harness's transcription-accuracy mode -- comparing our own ASR output against a
real, human-made reference (an embedded or sidecar subtitle in the SOURCE language), the
same way translation-accuracy mode compares translated output against a target-language
reference. See metrics.py/reference_extraction.py for the shared scoring machinery.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.pipeline.stage11_qc_output.formatters.srt import ParsedSrtCue


def canonical_transcript_to_cues(raw_json_path: str | Path) -> list[ParsedSrtCue]:
    """Excludes segments Stage 6 suppressed as hallucinations -- those never reach
    translation or output either, so leaving them out keeps this evaluation consistent
    with what the rest of the pipeline actually treats as real content."""
    data = json.loads(Path(raw_json_path).read_text(encoding="utf-8"))
    cues = []
    for i, seg in enumerate(data.get("segments", []), start=1):
        if seg.get("suppressed"):
            continue
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        cues.append(ParsedSrtCue(index=i, start=seg["start"], end=seg["end"], text=text))
    return cues
