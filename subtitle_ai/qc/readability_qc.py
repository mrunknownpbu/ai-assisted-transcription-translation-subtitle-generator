"""Readability QC: checks the final output cues against professional
subtitle constraints (CPL/CPS/duration). `population` = number of output
cues checked."""

from __future__ import annotations

from qc.types import QcCategory, QcFinding, QcResult, QcStage

MAX_LINE_CHARS = 42
MAX_LINES = 2
MIN_DURATION = 1.0
MAX_DURATION = 7.0
MAX_CPS = 21.0   # a little headroom over the 17 CPS target used to size cues


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
                                      f"duration {duration:.2f}s below minimum {MIN_DURATION}s", 0.7, index=i))
        elif duration > MAX_DURATION + 1e-6:
            findings.append(QcFinding(QcCategory.READABILITY_ERROR,
                                      f"duration {duration:.2f}s exceeds maximum {MAX_DURATION}s", 0.7, index=i))
        elif duration > 0 and len(text) / duration > MAX_CPS:
            findings.append(QcFinding(QcCategory.READABILITY_ERROR,
                                      f"reading speed {len(text)/duration:.1f} CPS exceeds {MAX_CPS}",
                                      0.5, index=i))
    return QcResult(stage=QcStage.READABILITY, population=len(cues), flagged=len(findings),
                    findings=findings)
