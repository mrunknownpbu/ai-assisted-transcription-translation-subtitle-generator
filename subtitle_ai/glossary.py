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


@dataclass
class PhraseEntry:
    """A forced whole-segment translation, distinct from Entity above:
    Entity protects a NAME (verbatim reinsertion via protect()/restore());
    PhraseEntry forces a fixed OUTPUT for an exact short source phrase.
    Added 2026-09-19 -- real QC evidence (jobs against a human-authored
    source .srt) showed NLLB hallucinating fabricated continuations for
    short, common, context-free utterances (e.g. "Bekle." -> "Wait, wait,
    wait. I got it.") because srt_translation.py translates one cue per
    span with no surrounding context. A glossary can't fix that by
    protecting a name; it can fix it by short-circuiting the model
    entirely for a known-exact phrase (see build_phrase_map/translate.py's
    translate_spans)."""
    source: str            # exact source-language phrase, e.g. "Peki."
    translation: str       # the forced English output, e.g. "Okay."
    language: str | None   # None = applies regardless of detected language


def _phrase_key(text: str) -> str:
    return text.strip().rstrip(".!?").casefold()


def build_phrase_map(phrases: list[PhraseEntry], detected_language: str | None) -> dict[str, str]:
    """_phrase_key(...) match -> forced translation, filtered to entries
    whose language is universal (None) or matches THIS job's detected
    source language. Trailing .!? is stripped before comparing (and is
    not part of the key), since real subtitle punctuation on short
    interjections varies ("Peki." / "Peki!" / "Peki?")."""
    return {_phrase_key(p.source): p.translation
           for p in phrases if p.language is None or p.language == detected_language}


def _placeholder(k: int) -> str:
    return "X" + chr(97 + k // 26) + chr(97 + k % 26)


def build_glossary(entities: list[Entity]) -> dict[str, tuple[str, str]]:
    """surface_form(ORIGINAL spelling) -> (placeholder, canonical),
    deduplicated case-insensitively via a separate casefold() tracker --
    never keyed by the casefolded form itself.

    Real defect (Turkish validation, 2026-09-19): Python's str.casefold()
    maps the Turkish capital dotted "İ" (U+0130) to a TWO-codepoint
    sequence "i̇" (i + combining dot above, U+0307) -- a different string
    than the real "İ" text. protect() below regex-matches directly
    against this dict's keys (relying on re.IGNORECASE for case-
    insensitivity, which does its own internal folding and never needed a
    pre-casefolded pattern) -- so when the key was the casefolded form,
    an entity like "İstanbul" silently never matched real "İstanbul" text
    at all: confirmed live, protect("İstanbul'da ...", glossary) left the
    name completely unprotected. An identical ASCII name worked fine,
    proving this was specifically the Turkish-İ casefold expansion, not
    the apostrophe boundary handling (already correct -- see _bounded()).
    """
    glossary: dict[str, tuple[str, str]] = {}
    seen_casefolded: set[str] = set()
    counter = 0
    for entity in entities:
        for form in entity.surface_forms:
            key = form.casefold()
            if key not in seen_casefolded:
                seen_casefolded.add(key)
                glossary[form] = (_placeholder(counter), entity.canonical)
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


def bare_entity_translation(protected_text: str, glossary: dict[str, tuple[str, str]]) -> str | None:
    """If `protected_text` (already protect()-ed) is nothing but one or
    more protected-entity placeholders plus punctuation/whitespace --
    real content = zero -- return the fully-restored text and let the
    caller skip the model entirely. Returns None the moment any word
    character survives after every placeholder is stripped out, so this
    only ever intercepts a genuinely bare name-call, never a real sentence
    that happens to also contain a protected name.

    Real evidence (S01E02 QC run, 2026-09-20): NLLB hallucinated
    "Cenk, what's going on?" from bare "Cenk." even though Cenk IS already
    a protected entity in that series' glossary -- protect()/restore()
    only guarantees the canonical spelling survives in whatever the model
    returns, it does nothing to stop the model padding out a placeholder-
    only input with invented text. Confirmed twice in the same run
    ("Sirius!" -> "Sirius, what are you doing?"). The fix is to never hand
    the model a degenerate input in the first place -- same reasoning as
    build_phrase_map's short-circuit for short context-free utterances,
    just triggered by shape (placeholder + punctuation only) instead of an
    exact-phrase match.

    `\\w` (not `[A-Za-z0-9]`) is used to detect leftover real content so
    this is correct for every source language this project supports, not
    just Latin-script ones (Python's `\\w` is Unicode-aware by default)."""
    if not glossary:
        return None
    residual = protected_text
    matched = False
    for placeholder, _canonical in glossary.values():
        residual, n = re.subn(_bounded(re.escape(placeholder)), "", residual, flags=re.IGNORECASE)
        if n:
            matched = True
    if not matched or re.search(r"\w", residual):
        return None
    return restore(protected_text, glossary)


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
