"""Unified QC schema.

Root cause this directly fixes: the prior implementation's frontend
rendered every QC stage's denominator as `summary.segments`, because
transcription QC and translation QC used different, ambiguous field names
for "how many things were checked" (`segments` vs `sentences`) and nothing
forced the frontend to know which. Real, confirmed consequence: the GUI
displayed "127/0 flagged" for translation QC when the real value was
"127/1992" -- the backend was always right, the frontend guessed wrong.

Fix: every QC result is one of these typed dataclasses with an explicit
`population` field. There is no field named `segments` or `sentences`
anywhere in this schema -- a caller (frontend or otherwise) that wants the
denominator reads `.population`, full stop, regardless of which QC stage
produced it. Ambiguity is structurally impossible, not just fixed by
convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class QcStage(str, Enum):
    TRANSCRIPTION = "transcription"
    HALLUCINATION = "hallucination"
    TRANSLATION = "translation"
    ENTITY = "entity"
    TIMING = "timing"
    SEGMENTATION = "segmentation"
    READABILITY = "readability"
    OUTPUT = "output"


class QcCategory(str, Enum):
    """Stable category enum -- QC findings use this, not free-text
    strings, so a category can be filtered/aggregated/tested reliably.
    A human-readable `reason` string still carries the specific detail."""
    CLEAN = "clean"
    DROPPED_UTTERANCE = "dropped_utterance"
    SUBSTITUTION = "substitution"
    HALLUCINATION = "hallucination"
    SEGMENTATION_DIFFERENCE = "segmentation_difference"
    NORMALIZATION_DIFFERENCE = "normalization_difference"
    TIMING_DIFFERENCE = "timing_difference"
    ENTITY_ERROR = "entity_error"
    TRANSLATION_ERROR = "translation_error"
    READABILITY_ERROR = "readability_error"
    UNKNOWN = "unknown"


@dataclass
class QcFinding:
    category: QcCategory
    reason: str
    confidence: float
    index: int | None = None          # segment/sentence/cue index this concerns
    evidence: dict = field(default_factory=dict)


@dataclass
class QcResult:
    """One QC stage's complete result. `population` is the authoritative
    denominator for this stage -- transcription's is a segment count,
    translation's is a sentence count, entity's is a protected-mention
    count, etc.; callers never need to know or guess which."""
    stage: QcStage
    population: int
    flagged: int
    findings: list[QcFinding] = field(default_factory=list)
    retried: int = 0
    improved: int = 0
    unresolved: int = 0

    def to_dict(self) -> dict:
        return {
            "stage": self.stage.value, "population": self.population, "flagged": self.flagged,
            "retried": self.retried, "improved": self.improved, "unresolved": self.unresolved,
            "findings": [{"category": f.category.value, "reason": f.reason,
                         "confidence": f.confidence, "index": f.index, "evidence": f.evidence}
                        for f in self.findings],
        }


@dataclass
class JobQc:
    """All QC results for one job, keyed by stage -- the API/GUI contract.
    `qc.transcription.population` / `qc.translation.population` are always
    the right field, by construction (see module docstring)."""
    transcription: QcResult | None = None
    hallucination: QcResult | None = None
    translation: QcResult | None = None
    entity: QcResult | None = None
    timing: QcResult | None = None
    segmentation: QcResult | None = None
    readability: QcResult | None = None
    output: QcResult | None = None

    def to_dict(self) -> dict:
        return {stage.value: getattr(self, stage.value).to_dict()
               for stage in QcStage if getattr(self, stage.value) is not None}
