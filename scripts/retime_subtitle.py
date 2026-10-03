#!/usr/bin/env python3
"""Re-time an existing subtitle against a video's audio, keeping its text.

    python scripts/retime_subtitle.py VIDEO SUBTITLE --out FIXED.srt
    python scripts/retime_subtitle.py VIDEO SUBTITLE --dry-run

The audio is transcribed with the production settings (a cached transcript of
that video is reused) and the subtitle is compared with it afterwards; the
subtitle never reaches ASR and its words are never changed. Prints what was
found: a constant shift, a frame-rate stretch, or steps, with the evidence.
Refuses (exit 2, nothing written) when the two share too little text.
See docs/decisions/2026-10-03-subtitle-retiming.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "subtitle_ai"))
import output
import retime
import retime_job
import srt


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video", type=Path)
    ap.add_argument("subtitle", type=Path)
    ap.add_argument("--out", type=Path, help="where to write the re-timed subtitle")
    ap.add_argument("--in-place", action="store_true", help="replace SUBTITLE (atomically) instead of --out")
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    ap.add_argument("--language", help="subtitle language code (default: the tag in its file name, e.g. film.tr.srt)")
    ap.add_argument("--cache-dir", default="/cache/transcripts")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args()
    if not args.dry_run and not args.out and not args.in_place:
        ap.error("give --out, --in-place or --dry-run")

    cues = srt.parse_lines(args.subtitle)
    language = args.language or retime_job.language_from_filename(args.subtitle.name)
    if not language:
        ap.error("cannot tell the subtitle's language from its name; give --language")
    words = retime_job.cached_words(args.video, args.cache_dir)
    if words is None:
        import tempfile

        from gpu import gpu_lock
        with tempfile.TemporaryDirectory() as tmp, gpu_lock():
            words = retime_job.transcribe_words(args.video, Path(tmp))
    flat = [srt.SrtCue(c.start, c.end, " ".join(c.lines)) for c in cues]
    try:
        times, report = retime.retime(flat, words, language)
    except retime.RetimeRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    summary = {"method": report.method, "cues": report.cues, "anchored_cues": report.anchored_cues,
               "residual_p50_s": round(report.residual_p50, 2), "residual_p95_s": round(report.residual_p95, 2),
               "pieces": [{"from_s": round(p.t0, 1), "to_s": round(p.t1, 1), "offset_at_start_s": round(p.offset0, 2),
                           "offset_at_end_s": round(p.offset_at(p.t1), 2)} for p in report.pieces]}
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"{report.method}: {report.anchored_cues}/{report.cues} cues matched the audio "
              f"(residual median {report.residual_p50:.2f} s, 95th {report.residual_p95:.2f} s)")
        for p in summary["pieces"]:
            print(f"  {p['from_s']:>8.1f}s - {p['to_s']:>8.1f}s: {p['offset_at_start_s']:+.2f} s -> {p['offset_at_end_s']:+.2f} s")
    if args.dry_run:
        return 0
    if not report.changed:
        print("already in step with the audio; nothing written")
        return 0
    fixed = [srt.SrtCueLines(s, e, c.lines) for c, (s, e) in zip(cues, times)]
    target = args.subtitle if args.in_place else args.out
    output.write_srt_atomic(target, srt.render(fixed), allow_overwrite=args.in_place or target.exists())
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
