"""Entity QC: wraps glossary.entity_occurrence_report() into the typed QC
schema. `population` = number of distinct protected entities that
actually occurred in this job's source text.

Only flags a SHORTFALL (source_count > target_count), never an excess --
matching pipeline.py's entity_recovery step exactly, which triggers on
the identical `src > tgt` condition and can only ever ADD a missing
mention back, never remove an extra one. Real evidence a mismatch this
QC used to flag both ways was never actionable in the over-generation
direction (Love Is In The Air S01E01/E02, 2026-09-19): 'Serkan' occurring
MORE often in translation than source (NLLB expanding a pronoun to the
character's name) was flagged as ENTITY_ERROR at confidence 1.0 despite
there being no code path that does -- or should -- remove it."""

from __future__ import annotations

from glossary import entity_occurrence_report
from qc.types import QcCategory, QcFinding, QcResult, QcStage


def run(source_protected: str, target_text: str, glossary_map: dict) -> QcResult:
    report = entity_occurrence_report(source_protected, target_text, glossary_map)
    findings = []
    for canonical, (source_count, target_count) in report.items():
        if source_count > target_count:
            findings.append(QcFinding(
                category=QcCategory.ENTITY_ERROR,
                reason=f"'{canonical}' occurs {source_count}x in source, {target_count}x in translation",
                confidence=1.0, evidence={"canonical": canonical, "source_count": source_count,
                                          "target_count": target_count}))
    return QcResult(stage=QcStage.ENTITY, population=len(report), flagged=len(findings),
                    findings=findings)
