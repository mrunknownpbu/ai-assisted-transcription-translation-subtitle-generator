"""Timing QC: monotonic ordering, positive duration, no overlaps in the
final output cue list. `population` = number of output cues checked."""

from __future__ import annotations

from qc.types import QcCategory, QcFinding, QcResult, QcStage


def run(cues: list) -> QcResult:
    findings = []
    prev_end = -1.0
    for i, cue in enumerate(cues):
        if cue.end <= cue.start:
            findings.append(QcFinding(QcCategory.TIMING_DIFFERENCE,
                                      f"non-positive duration ({cue.start} -> {cue.end})", 0.9, index=i))
        elif cue.start < prev_end - 1e-6:
            findings.append(QcFinding(QcCategory.TIMING_DIFFERENCE,
                                      f"overlaps previous cue (starts {cue.start:.3f}s, prior ends {prev_end:.3f}s)",
                                      0.9, index=i))
        prev_end = max(prev_end, cue.end)
    return QcResult(stage=QcStage.TIMING, population=len(cues), flagged=len(findings), findings=findings)
