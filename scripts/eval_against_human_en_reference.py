#!/usr/bin/env python3
"""Score a real production run against a human English subtitle
(`<stem>.en.hi.srt`, SDH-style -- never read by the pipeline itself, see
output.PROTECTED_SUFFIXES) two ways:

* gap coverage: for each human reference cue, is there ANY of our own
  source-language (`.ja.srt`) cues overlapping it? A reference cue with
  no overlapping source cue at all is a real signal the pipeline
  produced nothing where a human heard something -- the same shape as
  the VAD-merged-gap bug (see CLAUDE.md, 2026-09-28/29), just checked
  systematically instead of by hand on one reported episode.
* cue granularity: how finely our cues are cut compared with the human
  editor's (see fragmentation()). Measured separately for our source-
  language cues and our English cues, so a split made while building the
  source cues can be told apart from one added when the English is spread
  back over them.
* translation quality: pairs our own `.en.srt` cues with the human
  reference by time overlap (reusing eval_translation.py's pair_cues()
  and chrF, unmodified) and reports corpus chrF -- how close our ALREADY
  PRODUCED English subtitle is to a human one, not a re-translation
  through a different code path.

Run inside the app container (no GPU needed -- this only reads existing
.srt files):

    docker cp scripts/eval_against_human_en_reference.py subtitle-ai:/tmp/
    docker exec -e PYTHONPATH=/app subtitle-ai python /tmp/eval_against_human_en_reference.py \\
        --season "/data/media/drama/japanese/Hammer Session! (2010) {tvdb-177461}/Season 01" \\
        --episodes 1 --json /tmp/e01-vs-human.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_translation import chrf, chrf_stats, clean_reference, pair_cues  # noqa: E402


def parse_episodes(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        out.extend(range(int(lo), int(hi or lo) + 1))
    return out


def gap_coverage(source_cues, reference_cues, min_overlap_frac: float = 0.1) -> list[dict]:
    """Reference cues (human) with essentially no overlapping source
    (our own ASR) cue -- i.e. real content the pipeline produced nothing
    for. min_overlap_frac guards against tiny edge-of-cue overlaps
    counting as "covered"."""
    flagged = []
    for ref in reference_cues:
        dur = max(ref.end - ref.start, 1e-6)
        covered = 0.0
        for src in source_cues:
            overlap = min(ref.end, src.end) - max(ref.start, src.start)
            if overlap > 0:
                covered += overlap
        if covered < min_overlap_frac * dur:
            flagged.append({"start": round(ref.start, 1), "end": round(ref.end, 1),
                            "text": clean_reference(ref.text)[:120]})
    return flagged


_SENTENCE_END = re.compile(r"[.!?…。！？]['\"」』)\]]*$")
SHORT_CUE_CHARS = 10


def _is_song_or_sound(text: str) -> bool:
    stripped = text.lstrip("-( ")
    return stripped.startswith(("\"", "“", "♪")) or not clean_reference(text)


def fragmentation(cues, reference_cues) -> dict:
    """Cue granularity of `cues` (ours) against the human reference.

    * cues_per_human_cue: for each human dialogue cue (songs and pure sound
      descriptions skipped, as in pair_cues()), how many of our cues have
      their midpoint inside it. 1.0 means the same granularity as the
      human editor; the "Hey, can" / "you" / "read it?" case scores 3.
    * split_human_cue_rate: share of those human cues that 2+ of our cues
      fall inside.
    * short_cue_rate: share of our cues with <= SHORT_CUE_CHARS characters.
    * mid_sentence_end_rate: share of our cues (all but the last) that end
      without sentence-ending punctuation, i.e. that break a sentence.
    Also the plain mean duration and character count of each side."""
    def stats(cs):
        n = len(cs)
        return {"cues": n,
                "mean_duration": round(sum(c.end - c.start for c in cs) / n, 2) if n else None,
                "mean_chars": round(sum(len(c.text) for c in cs) / n, 1) if n else None}

    per_human = []
    for ref in reference_cues:
        if _is_song_or_sound(ref.text):
            continue
        per_human.append(sum(1 for c in cues if ref.start <= (c.start + c.end) / 2 < ref.end))
    counted = [k for k in per_human if k > 0]
    texts = [c.text.strip() for c in cues]
    return {
        "ours": stats(cues), "human": stats(reference_cues),
        "cues_per_human_cue": round(sum(counted) / len(counted), 3) if counted else None,
        "split_human_cue_rate": round(sum(1 for k in counted if k >= 2) / len(counted), 3) if counted else None,
        "short_cue_rate": round(sum(1 for t in texts if len(t) <= SHORT_CUE_CHARS) / len(texts), 3)
                          if texts else None,
        "mid_sentence_end_rate": round(sum(1 for t in texts[:-1] if not _SENTENCE_END.search(t))
                                       / (len(texts) - 1), 3) if len(texts) > 1 else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", required=True, help="folder holding <stem>SxxEyy.{ja,en,en.hi}.srt")
    ap.add_argument("--episodes", default="1", help="e.g. 1-5 or 1,3,5")
    ap.add_argument("--source-lang", default="ja")
    ap.add_argument("--worst", type=int, default=0,
                    help="also print the N lowest-chrF EN pairs (ours vs human, with timestamps)")
    ap.add_argument("--json")
    args = ap.parse_args()

    from srt import parse

    season = Path(args.season)
    all_gaps: list[dict] = []
    chrf_pairs: list[tuple[str, str]] = []
    scored_pairs: list[tuple] = []
    per_episode = []

    for number in parse_episodes(args.episodes):
        src_path = next(iter(season.glob(f"*E{number:02d}.{args.source_lang}.srt")), None)
        sys_en_path = next(iter(season.glob(f"*E{number:02d}.en.srt")), None)
        ref_path = next(iter(season.glob(f"*E{number:02d}.en.hi.srt")), None)
        if not (src_path and sys_en_path and ref_path):
            print(f"E{number:02d}: missing one of .{args.source_lang}.srt / .en.srt / .en.hi.srt -- skipped")
            continue

        source_cues = parse(src_path)
        sys_en_cues = parse(sys_en_path)
        ref_cues = parse(ref_path)

        frag = {"source": fragmentation(source_cues, ref_cues), "en": fragmentation(sys_en_cues, ref_cues)}
        for side in ("source", "en"):
            f = frag[side]
            print(f"E{number:02d} {side:>6} cues: {f['ours']['cues']} vs {f['human']['cues']} human | "
                  f"{f['cues_per_human_cue']} per human cue, {f['split_human_cue_rate']} of human cues split, "
                  f"short(<={SHORT_CUE_CHARS}ch)={f['short_cue_rate']}, "
                  f"mid-sentence ends={f['mid_sentence_end_rate']} | mean {f['ours']['mean_duration']}s/"
                  f"{f['ours']['mean_chars']}ch vs human {f['human']['mean_duration']}s/{f['human']['mean_chars']}ch")

        gaps = gap_coverage(source_cues, ref_cues)
        for g in gaps:
            g["episode"] = f"E{number:02d}"
        all_gaps.extend(gaps)

        pairs = pair_cues(sys_en_cues, ref_cues)
        ep_stats = [chrf_stats(sys_en_cues[i].text, ref) for i, ref in pairs]
        ep_chrf = chrf(ep_stats) if ep_stats else None
        for i, ref in pairs:
            chrf_pairs.append((sys_en_cues[i].text, ref))
            scored_pairs.append((chrf([chrf_stats(sys_en_cues[i].text, ref)]), f"E{number:02d}",
                                 sys_en_cues[i], ref))

        print(f"E{number:02d}: {len(ref_cues)} human cues, {len(gaps)} with no source coverage "
              f"({100 * len(gaps) / max(len(ref_cues), 1):.1f}%) | "
              f"{len(pairs)} EN pairs scored, chrF={ep_chrf if ep_chrf is None else round(ep_chrf, 2)}")
        per_episode.append({"episode": f"E{number:02d}", "human_cues": len(ref_cues),
                            "uncovered_gaps": len(gaps), "en_pairs": len(pairs),
                            "chrf": round(ep_chrf, 2) if ep_chrf is not None else None,
                            "fragmentation": frag})

    overall_chrf = chrf([chrf_stats(h, r) for h, r in chrf_pairs]) if chrf_pairs else None
    print(f"\nOverall: {len(all_gaps)} uncovered gaps across all episodes, "
          f"chrF={overall_chrf if overall_chrf is None else round(overall_chrf, 2)} over {len(chrf_pairs)} pairs")
    if all_gaps:
        print("\nWorst gaps (no source-language coverage at all):")
        for g in sorted(all_gaps, key=lambda g: g["end"] - g["start"], reverse=True)[:15]:
            print(f"  {g['episode']} {g['start']:.1f}-{g['end']:.1f}s: {g['text']!r}")

    if args.worst:
        print(f"\nWorst {args.worst} EN pairs by chrF:")
        for score, ep, cue, ref in sorted(scored_pairs, key=lambda p: p[0])[:args.worst]:
            print(f"  {ep} {cue.start:.1f}-{cue.end:.1f}s chrF={score:.1f}\n    ours:  {cue.text!r}\n"
                  f"    human: {ref!r}")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "season": str(season), "episodes": per_episode,
            "overall_chrf": round(overall_chrf, 2) if overall_chrf is not None else None,
            "total_uncovered_gaps": len(all_gaps), "gaps": all_gaps,
            "metric": "gap coverage vs .en.hi.srt human reference timing; "
                     "chrF (char 1-6, beta 2) of production .en.srt vs cleaned human reference"},
            indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
