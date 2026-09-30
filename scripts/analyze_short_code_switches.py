#!/usr/bin/env python3
"""Report short foreign-language candidate runs without changing translation.

The production override requires three independently qualifying clauses because
two-clause runs produced real false positives in the Turkish corpus. This
tool deliberately reports those two-clause candidates for human review,
including repeated-text risk markers, so a future detector change can be
measured against real subtitle data before it is enabled.

Example:
    PYTHONPATH=subtitle_ai uv run python scripts/analyze_short_code_switches.py \
        --root /data/media/turkish --source-lang tr --json /tmp/code-switches.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "subtitle_ai"))

from langid import (MIN_DETECT_CHARS, MIN_DETECT_CONFIDENCE, _clauses,  # noqa: E402
                    detect_text_language)


def candidate_runs(texts: list[str], source_lang: str, *, min_run: int = 2,
                   min_chars: int = MIN_DETECT_CHARS,
                   min_confidence: float = MIN_DETECT_CONFIDENCE) -> list[dict]:
    """Return consecutive, review-only non-source-language clause runs.

    This mirrors the production detector's handling of short clauses: they
    neither qualify nor break a candidate run. It intentionally does not
    apply the production three-clause acceptance threshold.
    """
    runs: list[dict] = []
    run_lang: str | None = None
    members: list[dict] = []

    def flush() -> None:
        if run_lang is None or len(members) < min_run:
            return
        normalized = {" ".join(item["text"].casefold().split()) for item in members}
        runs.append({
            "language": run_lang,
            "clauses": members.copy(),
            "sentence_indices": sorted({item["sentence_index"] for item in members}),
            "distinct_clause_count": len(normalized),
            "repeated_text_only": len(normalized) < min_run,
        })

    for sentence_index, text in enumerate(texts):
        for clause in _clauses(text):
            if len(clause) < min_chars:
                continue  # Cannot qualify and deliberately does not break a run.
            language, confidence = detect_text_language(clause)
            qualifies = (len(clause) >= min_chars and confidence >= min_confidence
                         and language != source_lang and language != "und")
            if qualifies and language == run_lang:
                members.append({"sentence_index": sentence_index, "text": clause,
                                "confidence": round(confidence, 4)})
            elif qualifies:
                flush()
                run_lang = language
                members = [{"sentence_index": sentence_index, "text": clause,
                            "confidence": round(confidence, 4)}]
            else:
                flush()
                run_lang, members = None, []
    flush()
    return runs


def analyze_srt(path: Path, source_lang: str, *, min_run: int) -> list[dict]:
    from srt import parse

    cues = parse(path)
    runs = candidate_runs([cue.text for cue in cues], source_lang, min_run=min_run)
    for run in runs:
        indices = run["sentence_indices"]
        first, last = cues[indices[0]], cues[indices[-1]]
        run.update({
            "source_path": str(path),
            "start": round(first.start, 1),
            "end": round(last.end, 1),
        })
    return runs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True, help="library root containing source-language SRT files")
    parser.add_argument("--source-lang", required=True, help="source SRT suffix, for example tr")
    parser.add_argument("--min-run", type=int, default=2,
                        help="review-only candidate length; production remains fixed at 3")
    parser.add_argument("--json", help="write complete candidate evidence to this path")
    args = parser.parse_args()
    if args.min_run < 1:
        parser.error("--min-run must be at least 1")

    paths = sorted(Path(args.root).rglob(f"*.{args.source_lang}.srt"))
    candidates = [run for path in paths for run in analyze_srt(path, args.source_lang, min_run=args.min_run)]
    risky = sum(run["repeated_text_only"] for run in candidates)
    print(f"Scanned {len(paths)} .{args.source_lang}.srt files; found {len(candidates)} "
          f"review-only {args.min_run}-clause candidate runs ({risky} repeated-text risk).")
    for run in candidates:
        print(f"{run['source_path']} {run['start']:.1f}-{run['end']:.1f}s "
              f"{run['language']} clauses={len(run['clauses'])} distinct={run['distinct_clause_count']}"
              f"{' REPEATED-TEXT-RISK' if run['repeated_text_only'] else ''}")
        for clause in run["clauses"]:
            print(f"  [{clause['sentence_index']}] p={clause['confidence']}: {clause['text']!r}")
    if args.json:
        Path(args.json).write_text(json.dumps({
            "root": str(Path(args.root)), "source_lang": args.source_lang,
            "candidate_min_run": args.min_run, "production_min_run": 3,
            "candidate_runs": candidates,
            "note": "Review-only candidates; this does not enable code-switch overrides.",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
