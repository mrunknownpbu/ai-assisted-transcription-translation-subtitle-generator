"""Output QC: final structural validity of the rendered SRT text itself --
the last gate before write, independent of everything upstream (catches a
rendering bug even if every earlier stage was clean). `population` = cue
count in the rendered file."""

from __future__ import annotations

from qc.types import QcCategory, QcFinding, QcResult, QcStage
from srt import parse


def run(srt_path) -> QcResult:
    cues = parse(srt_path)
    findings = []
    prev_end = -1.0
    for i, cue in enumerate(cues):
        if not cue.text.strip():
            findings.append(QcFinding(QcCategory.READABILITY_ERROR, "empty cue text", 1.0, index=i))
        if cue.end <= cue.start:
            findings.append(QcFinding(QcCategory.TIMING_DIFFERENCE, "non-positive duration", 1.0, index=i))
        elif cue.start < prev_end - 0.01:
            findings.append(QcFinding(QcCategory.TIMING_DIFFERENCE, "overlaps previous cue", 1.0, index=i))
        prev_end = max(prev_end, cue.end)
    return QcResult(stage=QcStage.OUTPUT, population=len(cues), flagged=len(findings), findings=findings)
