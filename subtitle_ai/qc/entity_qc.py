"""Entity QC: wraps glossary.entity_occurrence_report() into the typed QC
schema. `population` = number of distinct protected entities that
actually occurred in this job's source text."""

from __future__ import annotations

from glossary import entity_occurrence_report
from qc.types import QcCategory, QcFinding, QcResult, QcStage


def run(source_protected: str, target_text: str, glossary_map: dict) -> QcResult:
    report = entity_occurrence_report(source_protected, target_text, glossary_map)
    findings = []
    for canonical, (source_count, target_count) in report.items():
        if source_count != target_count:
            findings.append(QcFinding(
                category=QcCategory.ENTITY_ERROR,
                reason=f"'{canonical}' occurs {source_count}x in source, {target_count}x in translation",
                confidence=1.0, evidence={"canonical": canonical, "source_count": source_count,
                                          "target_count": target_count}))
    return QcResult(stage=QcStage.ENTITY, population=len(report), flagged=len(findings),
                    findings=findings)
