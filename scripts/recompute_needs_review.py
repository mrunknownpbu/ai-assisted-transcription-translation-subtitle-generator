#!/usr/bin/env python3
"""One-off: re-derive every stored job's `needs_review` after the
2026-09-28 readability change (duration findings moved from confidence 0.7
to readability_qc.DURATION_CONFIDENCE, below qc.types.REVIEW_CONFIDENCE).

Rewrites the confidence of stored readability duration findings in each
row's `qc` JSON so the JSON and the count agree, then recounts with the
same rule as JobQc.needs_review_count(). Dry run by default; pass --apply
to write. Back up first (scripts/backup_jobs_db.sh) -- this edits the live
job database in place.

Usage: recompute_needs_review.py [--db PATH] [--apply]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "subtitle_ai"))

from qc.readability_qc import DURATION_CONFIDENCE  # noqa: E402
from qc.types import REVIEW_CATEGORIES, REVIEW_CONFIDENCE  # noqa: E402

REVIEW_CATEGORY_VALUES = {c.value for c in REVIEW_CATEGORIES}
DEFAULT_DB = "/opt/docker/appdata/subtitle-ai/cache/jobs.db"


def _is_duration_finding(finding: dict) -> bool:
    return str(finding.get("reason", "")).startswith("duration ")


def recount(qc: dict) -> tuple[dict, int]:
    count = 0
    for stage, result in qc.items():
        for f in (result or {}).get("findings", []):
            if stage == "readability" and _is_duration_finding(f):
                f["confidence"] = DURATION_CONFIDENCE
            if f.get("category") in REVIEW_CATEGORY_VALUES or (f.get("confidence") or 0) >= REVIEW_CONFIDENCE:
                count += 1
    return qc, count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    rows = conn.execute("SELECT id, qc, needs_review FROM jobs WHERE qc IS NOT NULL").fetchall()
    before = after = changed = 0
    updates = []
    for job_id, qc_json, old in rows:
        qc, new = recount(json.loads(qc_json))
        before += old or 0
        after += new
        if new != (old or 0):
            changed += 1
            updates.append((json.dumps(qc), new, job_id))
    print(f"{len(rows)} jobs; needs_review total {before} -> {after}; {changed} rows change")
    if args.apply and updates:
        with conn:
            conn.executemany("UPDATE jobs SET qc = ?, needs_review = ? WHERE id = ?", updates)
        print("applied")
    elif updates:
        print("dry run -- pass --apply to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
