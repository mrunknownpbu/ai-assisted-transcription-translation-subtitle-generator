#!/usr/bin/env python3
"""Pull cast metadata for one series now and protect the names that pass
the evidence gate (cast_enrichment.py) -- the same thing the worker does
on its own every SUBTITLE_AI_CAST_REFRESH_DAYS while idle.

Run inside the app container (GPU for the probe, /glossary, /cache):

    docker cp scripts/refresh_cast.py subtitle-ai:/tmp/
    docker exec -w /app subtitle-ai python /tmp/refresh_cast.py \\
        "/data/media/drama/turkish/Love Is In The Air (2020) {tvdb-383383}" [--dry-run]

Prints every candidate with its decision; --dry-run writes nothing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("series_root", help="the series folder (its name carries {tvdb-<id>})")
    ap.add_argument("--glossary-dir", default="/glossary")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import cast_enrichment
    import glossary_profile

    root = Path(args.series_root)
    tvdb_id = glossary_profile.find_tvdb_id(root.name)
    if tvdb_id is None:
        print("the folder name carries no {tvdb-<id>} tag", file=sys.stderr)
        return 1
    report = cast_enrichment.enrich_series(tvdb_id, root, Path(args.glossary_dir), dry_run=args.dry_run)
    if not args.dry_run:
        cast_enrichment.save_report(report)
    print(f"series {tvdb_id}: language {report.get('language')}, "
          f"{report.get('episodes_with_subtitles')} episodes with subtitles, {report.get('seconds')}s")
    for c in sorted(report.get("candidates", []), key=lambda c: (c["decision"] != "protect", c["name"])):
        scope = ", ".join(c["scope"]) if c["scope"] else "series-wide"
        print(f"  {c['decision']:8s} {c['name']:12s} [{scope}] {c['reason']}")
    for flag in report.get("flags", []):
        print(f"  note: {flag}")
    print(("would protect: " if args.dry_run else "protected: ") + (", ".join(report.get("added", [])) or "nothing"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
