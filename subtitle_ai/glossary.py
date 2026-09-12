"""Entity protection: swap protected names/terms for opaque placeholders
before translation, restore them after. Confirmed reason this exists
(prior audit): NLLB reads unprotected Turkish names as ordinary
vocabulary -- "Cenk" (a name) literally means "war" and gets translated
as such. No episode-specific code: entities come entirely from a supplied
glossary (per-series data), not from anything hardcoded here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Real defect (Japanese validation, 2026-09-13): Python's \b is defined in
# terms of \w, which is Unicode-aware and treats CJK ideographs/kana as
# word characters too -- so \bNAME\b silently fails to match a name (or a
# placeholder spliced back in) embedded directly in continuous Japanese
# text with no surrounding whitespace, which is the NORMAL way Japanese is
# written. Redefining the boundary in terms of ASCII alphanumerics only
# fixes this: any adjacent CJK character automatically counts as a
# boundary (since it's outside this class), while Latin-script behavior
# (e.g. not matching "Cenk" inside "Cenkiz") is unchanged, since ASCII
# letters are still in the class on both sides.
_ASCII_WORD = "[A-Za-z0-9_]"


def _bounded(pattern: str) -> str:
    return rf"(?<!{_ASCII_WORD}){pattern}(?!{_ASCII_WORD})"


@dataclass
class Entity:
    canonical: str            # the form restored into the final translation
    surface_forms: list[str]  # every spelling/inflection worth protecting


def _placeholder(k: int) -> str:
    return "X" + chr(97 + k // 26) + chr(97 + k % 26)


def build_glossary(entities: list[Entity]) -> dict[str, tuple[str, str]]:
    """surface_form(casefolded) -> (placeholder, canonical)."""
    glossary: dict[str, tuple[str, str]] = {}
    counter = 0
    for entity in entities:
        for form in entity.surface_forms:
            key = form.casefold()
            if key not in glossary:
                glossary[key] = (_placeholder(counter), entity.canonical)
                counter += 1
    return glossary


def protect(text: str, glossary: dict[str, tuple[str, str]]) -> str:
    for form, (placeholder, _canonical) in sorted(glossary.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(_bounded(re.escape(form)), placeholder, text, flags=re.IGNORECASE)
    return text


def restore(text: str, glossary: dict[str, tuple[str, str]]) -> str:
    for _form, (placeholder, canonical) in glossary.items():
        text = re.sub(_bounded(re.escape(placeholder)), canonical, text, flags=re.IGNORECASE)
    return text


def occurrence_count(text: str, canonical: str) -> int:
    return len(re.findall(_bounded(re.escape(canonical)), text, re.IGNORECASE))


def recover_dropped_entities(source_protected: str, best_candidate: str,
                             glossary: dict[str, tuple[str, str]]) -> str:
    """The guaranteed last step when a translation candidate still
    under-counts a protected entity after real translation retries have
    already been tried. Deliberately takes NO model/tokenizer/device --
    structurally, not just by convention, this cannot call a translation
    model and therefore cannot invent new semantic content. It can only
    top up `best_candidate` with the entity's own canonical spelling.
    (Retained from the prior implementation's validated fix: an earlier
    version of this repair re-translated isolated fragments via NLLB and
    that path hallucinated free-form text -- removing the model call
    entirely, not tuning it, is what fixed it.)"""
    result = best_candidate
    trailing = source_protected.rstrip()[-1:] if source_protected.rstrip()[-1:] in ".!?" else ""
    for canonical, (source_count, target_count) in entity_occurrence_report(
            source_protected, best_candidate, glossary).items():
        missing = source_count - target_count
        if missing <= 0:
            continue
        insertion = " ".join(f"{canonical}{trailing}" for _ in range(missing))
        result = f"{insertion} {result}".strip() if result.strip() else insertion
    return result


def entity_occurrence_report(source_protected: str, target_text: str,
                             glossary: dict[str, tuple[str, str]]) -> dict[str, tuple[int, int]]:
    """canonical -> (source occurrence count, target occurrence count),
    for every entity actually present (protected) in the source. Feeds
    qc/entity_qc.py -- occurrence parity is the signal, not exact wording,
    since translation legitimately rephrases around a name."""
    report: dict[str, tuple[int, int]] = {}
    seen_canonical: set[str] = set()
    for _form, (placeholder, canonical) in glossary.items():
        if canonical in seen_canonical:
            continue
        source_count = len(re.findall(_bounded(re.escape(placeholder)), source_protected, re.IGNORECASE))
        if source_count == 0:
            continue
        seen_canonical.add(canonical)
        target_count = occurrence_count(target_text, canonical)
        report[canonical] = (source_count, target_count)
    return report
