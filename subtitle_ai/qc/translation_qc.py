"""Translation QC: per-sentence heuristic checks over NLLB output.
`population` = number of sentences assessed -- see qc/types.py's
docstring for exactly why this field, not `segments`, is what a caller
must read for this stage."""

from __future__ import annotations

import re

from qc.types import QcCategory, QcFinding, QcResult, QcStage

_PLACEHOLDER = re.compile(r"\bX[a-z]{2}\b")
_BRACKETS = {"(": ")", "[": "]", "{": "}"}


def _unbalanced_brackets(text: str) -> bool:
    stack = []
    for ch in text:
        if ch in _BRACKETS:
            stack.append(_BRACKETS[ch])
        elif ch in _BRACKETS.values():
            if not stack or stack.pop() != ch:
                return True
    return bool(stack)


def assess(source: str, translated: str) -> QcFinding | None:
    if not translated.strip():
        return QcFinding(QcCategory.TRANSLATION_ERROR, "empty translation", 1.0)
    if _PLACEHOLDER.search(translated):
        return QcFinding(QcCategory.ENTITY_ERROR, "leaked glossary placeholder", 0.9)
    if _unbalanced_brackets(translated):
        return QcFinding(QcCategory.TRANSLATION_ERROR, "unbalanced brackets", 0.6)
    src_len, tgt_len = len(source), len(translated)
    if src_len >= 15 and tgt_len < src_len * 0.25:
        return QcFinding(QcCategory.SUBSTITUTION, "suspiciously short vs. source length", 0.5)
    if src_len >= 5 and tgt_len > src_len * 3.5:
        return QcFinding(QcCategory.TRANSLATION_ERROR, "suspiciously long vs. source length", 0.5)
    return None


def run(sources: list[str], translations: list[str]) -> QcResult:
    findings = []
    for i, (s, t) in enumerate(zip(sources, translations)):
        finding = assess(s, t)
        if finding:
            finding.index = i
            findings.append(finding)
    seen: dict[str, int] = {}
    for i, (s, t) in enumerate(zip(sources, translations)):
        key = t.strip().casefold()
        if key and len(key) >= 8:
            prior = seen.get(key)
            if prior is not None and sources[prior].strip().casefold() != s.strip().casefold():
                findings.append(QcFinding(QcCategory.SUBSTITUTION,
                                          f"repeated translation matches unrelated sentence {prior}",
                                          0.5, index=i))
            seen[key] = i
    return QcResult(stage=QcStage.TRANSLATION, population=len(sources), flagged=len(findings),
                    findings=findings)
