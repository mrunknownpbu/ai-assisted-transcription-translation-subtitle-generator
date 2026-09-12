"""Transcription QC: wraps hallucination.py's per-segment findings (plus
plain low-confidence segments) into the typed QC schema. `population` =
number of ASR segments -- distinct from translation QC's sentence
population; see qc/types.py."""

from __future__ import annotations

from hallucination import HallucinationFinding
from qc.types import QcCategory, QcFinding, QcResult, QcStage
from transcript import Segment

LOW_CONFIDENCE_LOGPROB = -0.8


def run(segments: list[Segment], hallucination_findings: list[HallucinationFinding]) -> QcResult:
    findings = []
    for seg, h in zip(segments, hallucination_findings):
        if h.score > 0:
            category = QcCategory.HALLUCINATION if h.suppress else QcCategory.UNKNOWN
            findings.append(QcFinding(category=category, reason="; ".join(h.reasons),
                                      confidence=h.score, index=seg.index,
                                      evidence={"suppressed": h.suppress}))
        elif seg.avg_logprob <= LOW_CONFIDENCE_LOGPROB:
            findings.append(QcFinding(category=QcCategory.UNKNOWN,
                                      reason=f"low decoder confidence (avg_logprob={seg.avg_logprob:.2f})",
                                      confidence=0.3, index=seg.index))
    return QcResult(stage=QcStage.TRANSCRIPTION, population=len(segments), flagged=len(findings),
                    findings=findings)
