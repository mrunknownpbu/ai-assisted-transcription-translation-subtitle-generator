"""Entity/glossary QC — reconciles protected-entity occurrence counts between source and
translated text. Only meaningful (and only ever run) when a job supplied a glossary; a
count mismatch means `glossary.restore()`/`recover_dropped_entities()` didn't fully repair
a dropped or duplicated name, which is worth surfacing even though the platform's structural
recovery step already tries to fix it before this check ever sees the text.
"""
from __future__ import annotations

from app.pipeline.interfaces import QcFinding, QcReport
from app.pipeline.stage8_translation.glossary import entity_occurrence_report


def run(
    source_protected: str, target_text: str, glossary_map: dict[str, tuple[str, str]],
    target_language: str | None = None,
) -> QcReport:
    report = entity_occurrence_report(source_protected, target_text, glossary_map)
    findings: list[QcFinding] = []

    for canonical, counts in report.items():
        ok = counts["source_count"] == counts["target_count"]
        findings.append(QcFinding(
            check="entity_count_reconciled", passed=ok, cue_id=None,
            reason=(f"'{canonical}': source={counts['source_count']}, target={counts['target_count']}"
                    + ("" if ok else " — mismatch after translation and recovery")),
        ))

    passed = all(f.passed for f in findings)
    return QcReport(stage="entity_qc", target_language=target_language, passed=passed, findings=findings)
