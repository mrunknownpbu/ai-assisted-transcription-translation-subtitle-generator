#!/usr/bin/env python3
"""Rank completed jobs for *measurement-only* QC review prioritization.

This script reads a JSON export containing either a list of job objects, a
``{"jobs": [...]}`` object, or one job object.  It only reports a deterministic
ranking; it never opens a database, changes ``needs_review``, or alters QC
results or production behavior.

Usage:
    python scripts/analyze_qc_review_priority.py --input completed-jobs.json
    python scripts/analyze_qc_review_priority.py --input completed-jobs.json --limit 25

The JSON report contains the fixed scoring policy and per-job feature
contributions so operators can reproduce why a job was ranked.  Malformed
optional fields are ignored, and invalid JSON or an unsupported top-level
shape exits safely with status 2.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


MAX_INPUT_BYTES = 20 * 1024 * 1024

# Fixed weights intentionally live beside the report policy, rather than in
# production QC code. They can be evaluated before any future policy change.
SCORING_POLICY = {
    "entity_finding": "4 + 4 * confidence per entity_error finding",
    "hallucination_finding": "4 + 4 * confidence per hallucination finding",
    "translation_finding": "3 * confidence per translation_error finding",
    "other_high_confidence": "2 * (confidence - 0.7) for other findings above 0.7",
    "flagged_rate": "2 * flagged / population per QC stage, capped at 2",
    "unresolved": "0.5 per unresolved item, capped at 2 per QC stage",
    "needs_review": "0.2 per stored needs_review item, capped at 2",
    "log_severity": "1 per error and 0.25 per warning log entry, capped at 2",
}


def _number(value: object, default: float = 0.0) -> float:
    """Return a finite non-negative number without trusting JSON types."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else default


def _add(contributions: list[dict[str, Any]], feature: str, value: float, **details: Any) -> None:
    if value > 0:
        contributions.append({"feature": feature, "value": round(value, 4), **details})


def _completed_jobs(document: object) -> list[Mapping[str, Any]]:
    """Extract supported JSON shapes without accepting arbitrary objects."""
    if isinstance(document, list):
        jobs = document
    elif isinstance(document, Mapping) and isinstance(document.get("jobs"), list):
        jobs = document["jobs"]
    elif isinstance(document, Mapping):
        jobs = [document]
    else:
        raise ValueError("input must be a job object, a job list, or an object with a jobs list")
    return [job for job in jobs if isinstance(job, Mapping) and job.get("status") == "completed"]


def score_job(job: Mapping[str, Any], ordinal: int = 0) -> dict[str, Any]:
    """Score one completed job using only its already-recorded QC/log signals."""
    contributions: list[dict[str, Any]] = []
    qc = job.get("qc")
    if isinstance(qc, Mapping):
        for stage in sorted(qc, key=str):
            result = qc[stage]
            if not isinstance(result, Mapping):
                continue
            findings = result.get("findings")
            if isinstance(findings, list):
                for finding in findings:
                    if not isinstance(finding, Mapping):
                        continue
                    category = str(finding.get("category", "")).lower()
                    confidence = min(1.0, _number(finding.get("confidence")))
                    if category == "entity_error":
                        _add(contributions, "entity_finding", 4 + 4 * confidence,
                             stage=str(stage), category=category)
                    elif category == "hallucination":
                        _add(contributions, "hallucination_finding", 4 + 4 * confidence,
                             stage=str(stage), category=category)
                    elif category == "translation_error":
                        _add(contributions, "translation_finding", 3 * confidence,
                             stage=str(stage), category=category)
                    elif confidence > 0.7:
                        _add(contributions, "other_high_confidence", 2 * (confidence - 0.7),
                             stage=str(stage), category=category)

            population = _number(result.get("population"))
            flagged = _number(result.get("flagged"))
            if population:
                _add(contributions, "flagged_rate", min(2.0, 2 * flagged / population),
                     stage=str(stage), flagged=flagged, population=population)
            _add(contributions, "unresolved", min(2.0, 0.5 * _number(result.get("unresolved"))),
                 stage=str(stage))

    _add(contributions, "needs_review", min(2.0, 0.2 * _number(job.get("needs_review"))))
    log = job.get("log")
    if isinstance(log, list):
        errors = sum(isinstance(item, Mapping) and str(item.get("level", "")).lower() == "error"
                     for item in log)
        warnings = sum(isinstance(item, Mapping) and str(item.get("level", "")).lower() == "warning"
                       for item in log)
        _add(contributions, "log_severity", min(2.0, errors + 0.25 * warnings),
             errors=errors, warnings=warnings)

    identifier = str(job.get("id") or job.get("video_path") or f"job-{ordinal:08d}")
    score = round(sum(item["value"] for item in contributions), 4)
    return {"job_id": identifier, "score": score, "contributions": contributions}


def rank_jobs(document: object, limit: int | None = None) -> dict[str, Any]:
    """Produce a deterministic, read-only report from an already-parsed export."""
    jobs = _completed_jobs(document)
    candidates = [score_job(job, ordinal) for ordinal, job in enumerate(jobs)]
    candidates = [candidate for candidate in candidates if candidate["score"] > 0]
    candidates.sort(key=lambda candidate: (-candidate["score"], candidate["job_id"]))
    if limit is not None:
        candidates = candidates[:limit]
    for position, candidate in enumerate(candidates, 1):
        candidate["rank"] = position
    return {
        "schema_version": 1,
        "measurement_only": True,
        "scoring_policy": SCORING_POLICY,
        "completed_jobs_seen": len(jobs),
        "review_candidates": candidates,
    }


def load_document(path: Path) -> object:
    """Read a bounded UTF-8 JSON export; no input is written or modified."""
    try:
        if not path.is_file():
            raise ValueError("input must be a regular file")
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"input exceeds {MAX_INPUT_BYTES} bytes")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read JSON input: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, required=True, help="JSON job export; read only")
    parser.add_argument("--limit", type=int, default=None, help="maximum ranked candidates to report")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be non-negative")
    try:
        report = rank_jobs(load_document(args.input), args.limit)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
