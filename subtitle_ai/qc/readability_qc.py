"""Readability QC: checks the final output cues against professional
subtitle constraints (CPL/CPS/duration). `population` = number of output
cues checked."""

from __future__ import annotations

from qc.types import QcCategory, QcFinding, QcResult, QcStage
from subtitle_constraints import (MAX_CHARS_PER_LINE as CONFIG_MAX_LINE_CHARS,
                                  MAX_CPS, MAX_CUE_DURATION,
                                  MAX_LINES as CONFIG_MAX_LINES,
                                  MIN_CUE_DURATION)

MAX_LINE_CHARS = CONFIG_MAX_LINE_CHARS
MAX_LINES = CONFIG_MAX_LINES
MIN_DURATION = MIN_CUE_DURATION
MAX_DURATION = MAX_CUE_DURATION
# Duration findings sit BELOW qc.types.REVIEW_CONFIDENCE (0.7) on purpose:
# they're about timing, which the review editor can't change (text only),
# and in Workflow B the timing is inherited from the uploaded source SRT
# anyway. At 0.7 the min-duration rule alone was 62,490 of the 62,512
# needs_review hits across all 300 production jobs (2026-09-28), burying
# the 21 real entity/hallucination findings the count exists to surface.
DURATION_CONFIDENCE = 0.5


def run(cues: list) -> QcResult:
    findings = []
    for i, cue in enumerate(cues):
        lines = getattr(cue, "lines", None) or [cue.text]
        duration = cue.end - cue.start
        text = cue.text if hasattr(cue, "text") else " ".join(lines)
        if len(lines) > MAX_LINES:
            findings.append(QcFinding(QcCategory.READABILITY_ERROR,
                                      f"{len(lines)} lines exceeds max {MAX_LINES}", 0.8, index=i))
        elif any(len(l) > MAX_LINE_CHARS for l in lines):
            findings.append(QcFinding(QcCategory.READABILITY_ERROR,
                                      "a line exceeds max character length", 0.6, index=i))
        elif duration < MIN_DURATION - 1e-6:
            findings.append(QcFinding(QcCategory.READABILITY_ERROR,
                                      f"duration {duration:.2f}s below minimum {MIN_DURATION}s",
                                      DURATION_CONFIDENCE, index=i))
        elif duration > MAX_DURATION + 1e-6:
            findings.append(QcFinding(QcCategory.READABILITY_ERROR,
                                      f"duration {duration:.2f}s exceeds maximum {MAX_DURATION}s",
                                      DURATION_CONFIDENCE, index=i))
        elif duration > 0 and len(text) / duration > MAX_CPS:
            findings.append(QcFinding(QcCategory.READABILITY_ERROR,
                                      f"reading speed {len(text)/duration:.1f} CPS exceeds {MAX_CPS}",
                                      0.5, index=i))
    return QcResult(stage=QcStage.READABILITY, population=len(cues), flagged=len(findings),
                    findings=findings)
