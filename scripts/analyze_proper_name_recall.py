#!/usr/bin/env python3
"""Measure proper-name ASR recall without changing production output.

Compare an already-produced source SRT with a human source-language reference.
Names come from either a pipeline glossary YAML (its ``entities`` entries) or
a UTF-8, one-name-per-line file.  A matching production cue must overlap the
reference cue in time.  When an alias for the same glossary entity is present,
the result is reported as an exact-form mismatch; when no production cue
overlaps, it is reported as absent content.

Examples:
    PYTHONPATH=subtitle_ai uv run python scripts/analyze_proper_name_recall.py \
        --production /data/episode.ja.srt --reference /data/episode.ja.hi.srt \
        --glossary /glossary/tvdb-123.yaml --json name-recall.json

    PYTHONPATH=subtitle_ai uv run python scripts/analyze_proper_name_recall.py \
        --production episode.tr.srt --reference episode.tr.hi.srt \
        --names-file character-names.txt

This is measurement-only: it reads existing files and never edits subtitles,
glossaries, or pipeline configuration.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "subtitle_ai"))

_ASCII_WORD = r"A-Za-z0-9_"


class NameForm(NamedTuple):
    canonical: str
    form: str


def _normalise_form(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _unique_forms(forms: list[NameForm]) -> list[NameForm]:
    """Keep the first entity for duplicate spellings, deterministically."""
    seen: set[str] = set()
    out = []
    for item in forms:
        key = item.form.casefold()
        if item.form and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def forms_from_name_lines(lines: list[str]) -> list[NameForm]:
    """Read one literal expected name per line, skipping blank/comment lines."""
    return _unique_forms([
        NameForm(form, form)
        for line in lines
        if (form := _normalise_form(line)) and not form.startswith("#")
    ])


def forms_from_glossary_data(data: dict) -> list[NameForm]:
    """Extract canonical names and surface forms from the existing YAML schema."""
    if not isinstance(data, dict):
        raise ValueError("glossary root must be a mapping")
    out: list[NameForm] = []
    for entity in data.get("entities", []):
        if not isinstance(entity, dict):
            continue
        canonical = _normalise_form(str(entity.get("canonical", "")))
        raw_forms = entity.get("surface_forms", [])
        if isinstance(raw_forms, str):
            raw_forms = [raw_forms]
        if not canonical or not isinstance(raw_forms, list):
            continue
        out.append(NameForm(canonical, canonical))
        out.extend(NameForm(canonical, _normalise_form(str(form))) for form in raw_forms)
    return _unique_forms(out)


def _form_pattern(form: str) -> re.Pattern[str]:
    # Use the pipeline's ASCII-only boundary rule: CJK text has no word
    # spaces, while Latin forms must not match inside a longer Latin word.
    escaped = re.escape(form).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![{_ASCII_WORD}]){escaped}(?![{_ASCII_WORD}])", re.IGNORECASE)


def mentions(text: str, forms: list[NameForm]) -> list[NameForm]:
    """Return non-overlapping, longest-first proper-name mentions in `text`."""
    text = unicodedata.normalize("NFKC", text)
    candidates: list[tuple[int, int, NameForm]] = []
    for name in forms:
        candidates.extend((match.start(), match.end(), name)
                          for match in _form_pattern(name.form).finditer(text))
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2].form.casefold()))
    selected: list[NameForm] = []
    end = -1
    for start, stop, name in candidates:
        if start >= end:
            selected.append(name)
            end = stop
    return selected


def _overlapping_text(reference_cue, production_cues) -> str:
    return " ".join(cue.text for cue in production_cues
                    if min(reference_cue.end, cue.end) > max(reference_cue.start, cue.start))


def analyze_cues(production_cues, reference_cues, forms: list[NameForm]) -> dict:
    """Score reference name mentions against time-overlapping production cues."""
    per_form: dict[str, Counter] = defaultdict(Counter)
    evidence = []
    for reference in reference_cues:
        production_text = _overlapping_text(reference, production_cues)
        observed = mentions(production_text, forms)
        observed_by_canonical: dict[str, set[str]] = defaultdict(set)
        for item in observed:
            observed_by_canonical[item.canonical].add(item.form)

        for expected in mentions(reference.text, forms):
            row = per_form[expected.form]
            row["reference"] += 1
            if expected.form in observed_by_canonical[expected.canonical]:
                row["exact_hits"] += 1
                status = "exact_hit"
            elif observed_by_canonical[expected.canonical]:
                row["exact_form_mismatches"] += 1
                status = "exact_form_mismatch"
            elif not production_text:
                row["absent_content"] += 1
                status = "absent_content"
            else:
                row["name_absent_in_production_content"] += 1
                status = "name_absent_in_production_content"

            evidence.append({
                "form": expected.form,
                "canonical": expected.canonical,
                "status": status,
                "reference_start": round(reference.start, 3),
                "reference_end": round(reference.end, 3),
                "reference_text": reference.text,
                "production_text": production_text,
                "observed_same_entity_forms": sorted(observed_by_canonical[expected.canonical]),
            })

    form_rows = []
    for name in forms:
        counts = per_form[name.form]
        reference_count = counts["reference"]
        if not reference_count:
            continue
        form_rows.append({
            "form": name.form,
            "canonical": name.canonical,
            "reference_occurrences": reference_count,
            "exact_hits": counts["exact_hits"],
            "recall": round(counts["exact_hits"] / reference_count, 4),
            "misses": reference_count - counts["exact_hits"],
            "exact_form_mismatches": counts["exact_form_mismatches"],
            "absent_content": counts["absent_content"],
            "name_absent_in_production_content": counts["name_absent_in_production_content"],
        })
    total_reference = sum(row["reference_occurrences"] for row in form_rows)
    total_hits = sum(row["exact_hits"] for row in form_rows)
    return {
        "summary": {
            "reference_name_occurrences": total_reference,
            "exact_hits": total_hits,
            "exact_recall": round(total_hits / total_reference, 4) if total_reference else None,
            "exact_form_mismatches": sum(row["exact_form_mismatches"] for row in form_rows),
            "absent_content": sum(row["absent_content"] for row in form_rows),
            "name_absent_in_production_content": sum(
                row["name_absent_in_production_content"] for row in form_rows),
        },
        "forms": form_rows,
        "missed_forms": [row for row in form_rows if row["misses"]],
        "evidence": evidence,
    }


def load_forms(args) -> list[NameForm]:
    if args.glossary:
        import yaml

        data = yaml.safe_load(Path(args.glossary).read_text(encoding="utf-8")) or {}
        return forms_from_glossary_data(data)
    return forms_from_name_lines(Path(args.names_file).read_text(encoding="utf-8").splitlines())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--production", required=True, help="production source-language SRT")
    parser.add_argument("--reference", required=True, help="human source-reference SRT")
    names = parser.add_mutually_exclusive_group(required=True)
    names.add_argument("--glossary", help="pipeline glossary YAML; reads entities only")
    names.add_argument("--names-file", help="UTF-8 file of one expected name form per line")
    parser.add_argument("--json", help="write the complete deterministic report to this path")
    args = parser.parse_args()

    from srt import parse

    forms = load_forms(args)
    if not forms:
        parser.error("the supplied glossary or names file contains no usable names")
    report = analyze_cues(parse(Path(args.production)), parse(Path(args.reference)), forms)
    report.update({
        "production": str(Path(args.production)),
        "reference": str(Path(args.reference)),
        "name_source": str(Path(args.glossary or args.names_file)),
        "metric": ("exact proper-name form recall against human source-reference SRT; "
                   "time-overlap distinguishes no production content from a name absent in content"),
    })

    summary = report["summary"]
    recall = summary["exact_recall"]
    recall_text = f"{recall * 100:.1f}%" if recall is not None else "n/a"
    print(f"Exact proper-name recall: {summary['exact_hits']}/{summary['reference_name_occurrences']} "
          f"({recall_text})")
    print("Missed forms:")
    for row in report["missed_forms"]:
        print(f"  {row['form']!r}: {row['misses']}/{row['reference_occurrences']} missed; "
              f"exact-form mismatch={row['exact_form_mismatches']}, "
              f"absent content={row['absent_content']}, "
              f"name absent in content={row['name_absent_in_production_content']}")
    if not report["missed_forms"]:
        print("  none")
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
