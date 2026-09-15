"""Entity/glossary protection for translation — ported near-verbatim from a sibling
project's proven implementation (byte-identical between both sibling projects, so this is
the one design that needed no reconciliation).

Mechanism: opaque short ASCII placeholder substitution, not a model-side constraint. Named
entities (character names, places) are swapped for placeholders before translation and
restored by string substitution after, so the translation model never sees — and therefore
can never mistranslate or transliterate — the actual name.

Entirely optional and gated on the caller supplying entities: a job with no glossary never
touches this module (`protect`/`restore` are no-ops on an empty glossary map).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


__all__ = [
    "Entity", "build_glossary", "protect", "restore", "occurrence_count",
    "entity_occurrence_report", "recover_dropped_entities",
]


@dataclass(frozen=True)
class Entity:
    canonical: str            # the form restored into the final translation
    surface_forms: list[str] = field(default_factory=list)  # every spelling/inflection worth protecting


def _placeholder(k: int) -> str:
    return "X" + chr(97 + k // 26) + chr(97 + k % 26)  # Xaa, Xab, Xac, ...


def build_glossary(entities: list[Entity]) -> dict[str, tuple[str, str]]:
    """surface_form (casefolded) -> (placeholder, canonical)."""
    glossary: dict[str, tuple[str, str]] = {}
    k = 0
    for entity in entities:
        forms = entity.surface_forms or [entity.canonical]
        for form in forms:
            key = form.strip().casefold()
            if not key or key in glossary:
                continue
            glossary[key] = (_placeholder(k), entity.canonical)
            k += 1
    return glossary


# Python's `\b` is Unicode-word-aware and fails against scripts with no whitespace (e.g.
# Japanese) — a real bug this custom boundary fixes: `\b` matched inside a run of CJK
# characters with no word gap at all, corrupting unrelated text around a protected name.
_ASCII_WORD = "[A-Za-z0-9_]"


def _bounded(pattern: str) -> str:
    return rf"(?<!{_ASCII_WORD}){pattern}(?!{_ASCII_WORD})"


def protect(text: str, glossary: dict[str, tuple[str, str]]) -> str:
    """Longest-surface-form-first so a short form (e.g. a first name) can't shadow-match
    inside a longer one (e.g. a full name) before the longer form gets its turn."""
    for form, (placeholder, _canonical) in sorted(glossary.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(_bounded(re.escape(form)), placeholder, text, flags=re.IGNORECASE)
    return text


def restore(text: str, glossary: dict[str, tuple[str, str]]) -> str:
    for _form, (placeholder, canonical) in glossary.items():
        text = re.sub(_bounded(re.escape(placeholder)), canonical, text, flags=re.IGNORECASE)
    return text


def occurrence_count(text: str, needle: str) -> int:
    return len(re.findall(_bounded(re.escape(needle)), text, flags=re.IGNORECASE))


def entity_occurrence_report(source_protected: str, target_text: str, glossary: dict[str, tuple[str, str]]) -> dict:
    """canonical -> {"source_count": int, "target_count": int}, one entry per distinct
    canonical entity (a canonical may have several surface forms/placeholders collapsed to
    one row)."""
    report: dict[str, dict[str, int]] = {}
    seen_placeholders: set[str] = set()
    for _form, (placeholder, canonical) in glossary.items():
        if placeholder in seen_placeholders:
            continue
        seen_placeholders.add(placeholder)
        report[canonical] = {
            "source_count": occurrence_count(source_protected, placeholder),
            "target_count": (
                occurrence_count(target_text, placeholder) + occurrence_count(target_text, canonical)
            ),
        }
    return report


_TRAILING_PUNCT_RE = re.compile(r"([.!?,;:]*)$")


def recover_dropped_entities(source_protected: str, best_candidate: str, glossary: dict[str, tuple[str, str]]) -> str:
    """Deliberately takes NO model/tokenizer/device — structurally, not just by
    convention, this cannot call a translation model and therefore cannot invent new
    semantic content. An earlier version of this repair re-translated isolated fragments
    via the model to recover a dropped name, and that path itself hallucinated free-form
    text; removing the model call entirely and replacing it with plain string insertion is
    what fixed it.

    If the target under-counts a protected entity relative to the source, prepends the
    missing number of plain-text copies of the canonical name to the result — a crude but
    honest repair that never claims to be a real translation of the surrounding sentence.
    """
    report = entity_occurrence_report(source_protected, best_candidate, glossary)
    trailing_match = _TRAILING_PUNCT_RE.search(best_candidate.rstrip())
    trailing_punct = trailing_match.group(1) if trailing_match else ""

    missing_prefix_parts = []
    for canonical, counts in report.items():
        missing = counts["source_count"] - counts["target_count"]
        if missing > 0:
            missing_prefix_parts.extend([f"{canonical}{trailing_punct}"] * missing)

    if not missing_prefix_parts:
        return best_candidate
    return " ".join(missing_prefix_parts) + " " + best_candidate
