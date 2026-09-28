#!/usr/bin/env python3
"""Measure the decoder signals hallucination.py scores, over real cached
transcripts, before changing how they are scored.

Motivating case (Hammer Session! S01E01, 2026-09-29): a 25.4s segment at
3124.7s decoded as "ー" repeated ~80 times, compression_ratio=51.46, yet
hallucination_score=0.5 and not suppressed -- compression_ratio is scored
as a step (any value >= 2.4 adds exactly 0.5), so 51 counts the same as
2.5. Before making that graduated, this reports what real values look like:

* the compression_ratio distribution (percentiles) across every segment,
  and separately for segments that look like a repetition loop (few
  distinct characters in a long text) and for already-suppressed ones;
* every segment at or above --list-above, with its other signals and
  text, for reading by hand;
* with --what-if-span S: which segments a graduated compression score,
  0.5 + 0.5 * (ratio - 2.4) / S (capped at 1.0), would newly suppress.
  The candidate replaces only the compression term (other signals are
  kept via the stored score, so the result is a lower bound when
  recurrence also fired). Read that list: anything in it that is real
  dialogue is a false positive the candidate would introduce.

Run inside the app container (reads the cache only, no GPU):

    docker cp scripts/hallucination_signal_distribution.py subtitle-ai:/tmp/
    docker exec -e PYTHONPATH=/app subtitle-ai python /tmp/hallucination_signal_distribution.py \\
        --media "Hammer Session!" --media "Love Is In The Air" --what-if-span 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

COMPRESSION_RATIO_THRESHOLD = 2.4  # hallucination.COMPRESSION_RATIO_THRESHOLD
SUPPRESSION_THRESHOLD = 0.75       # hallucination.SUPPRESSION_THRESHOLD


def segment_text(seg: dict) -> str:
    """Words joined without the app's language-aware rendering -- enough
    to judge by eye and to count distinct characters."""
    return "".join(w.get("text", "") for w in seg.get("words", [])).strip()


def looks_like_repetition_loop(text: str, min_chars: int = 10, max_distinct_ratio: float = 0.2) -> bool:
    """A long text built from very few distinct characters ("ーーーー...",
    "ははははは..."): the shape of a decoder repetition loop."""
    chars = text.replace(" ", "")
    return len(chars) >= min_chars and len(set(chars)) / len(chars) <= max_distinct_ratio


def graduated_compression_score(ratio: float, span: float) -> float:
    if ratio < COMPRESSION_RATIO_THRESHOLD:
        return 0.0
    return min(1.0, 0.5 + 0.5 * (ratio - COMPRESSION_RATIO_THRESHOLD) / span)


def percentiles(values: list[float], points=(50, 90, 95, 99, 99.9, 100)) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    return {f"p{p}": round(ordered[min(len(ordered) - 1, int(len(ordered) * p / 100))], 2) for p in points}


def collect(cache_dir: Path, media_filters: list[str], pipeline_version: str | None) -> list[dict]:
    rows = []
    for path in sorted(cache_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        media = data.get("media_path", "")
        if media_filters and not any(f in media for f in media_filters):
            continue
        if pipeline_version and data.get("pipeline_version") != pipeline_version:
            continue
        for seg in data.get("segments", []):
            text = segment_text(seg)
            rows.append({
                "media": Path(media).name, "pipeline_version": data.get("pipeline_version"),
                "start": round(seg["start"], 1), "end": round(seg["end"], 1),
                "compression_ratio": seg.get("compression_ratio", 0.0),
                "no_speech_prob": seg.get("no_speech_prob", 0.0),
                "avg_logprob": seg.get("avg_logprob", 0.0),
                "score": seg.get("hallucination_score", 0.0), "suppressed": seg.get("suppressed", False),
                "loop_shaped": looks_like_repetition_loop(text), "text": text})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", default="/cache/transcripts")
    ap.add_argument("--media", action="append", default=[], help="substring of media_path (repeatable)")
    ap.add_argument("--pipeline-version", help="only transcripts cached by this pipeline version")
    ap.add_argument("--list-above", type=float, default=COMPRESSION_RATIO_THRESHOLD)
    ap.add_argument("--what-if-span", type=float)
    ap.add_argument("--json")
    args = ap.parse_args()

    rows = collect(Path(args.cache), args.media, args.pipeline_version)
    if not rows:
        print("no segments found")
        return 1
    ratios = [r["compression_ratio"] for r in rows]
    report = {
        "transcripts": len({(r["media"], r["pipeline_version"]) for r in rows}),
        "segments": len(rows),
        "compression_ratio_all": percentiles(ratios),
        "compression_ratio_loop_shaped": percentiles([r["compression_ratio"] for r in rows if r["loop_shaped"]]),
        "compression_ratio_suppressed": percentiles([r["compression_ratio"] for r in rows if r["suppressed"]]),
        "at_or_above_threshold": sum(1 for x in ratios if x >= COMPRESSION_RATIO_THRESHOLD),
        "loop_shaped": sum(1 for r in rows if r["loop_shaped"]),
    }
    print(json.dumps({k: v for k, v in report.items()}, indent=2, ensure_ascii=False))

    above = sorted((r for r in rows if r["compression_ratio"] >= args.list_above),
                   key=lambda r: r["compression_ratio"], reverse=True)
    print(f"\n{len(above)} segments with compression_ratio >= {args.list_above}:")
    for r in above:
        print(f"  cr={r['compression_ratio']:6.2f} nsp={r['no_speech_prob']:.2f} lp={r['avg_logprob']:6.3f} "
              f"score={r['score']:.2f}{' SUPPRESSED' if r['suppressed'] else ''}"
              f"{' loop' if r['loop_shaped'] else ''} | {r['media']} {r['start']}-{r['end']}s | {r['text'][:80]!r}")

    if args.what_if_span:
        newly = [r for r in rows if not r["suppressed"]
                 and max(r["score"], graduated_compression_score(r["compression_ratio"], args.what_if_span))
                 >= SUPPRESSION_THRESHOLD]
        report["what_if"] = {"span": args.what_if_span, "newly_suppressed": newly}
        print(f"\nwhat-if span={args.what_if_span}: {len(newly)} segments newly suppressed "
              f"(read each -- real dialogue here is a false positive):")
        for r in newly:
            print(f"  cr={r['compression_ratio']:6.2f} | {r['media']} {r['start']}-{r['end']}s | {r['text'][:80]!r}")

    if args.json:
        report["listed"] = above
        Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
