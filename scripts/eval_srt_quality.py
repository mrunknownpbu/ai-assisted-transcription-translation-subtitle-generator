#!/usr/bin/env python3
"""Compare a reference SRT with a candidate SRT as an evaluation artifact.

This script never imports the pipeline and never supplies subtitle text to
ASR.  It reports structural, readability, text-edit and timing diagnostics.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "subtitle_ai"))
import srt
from subtitle_constraints import MAX_CHARS_PER_LINE, MAX_CPS, MAX_CUE_DURATION, MIN_CUE_DURATION


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold(), flags=re.UNICODE)


def edit_counts(reference: list[str], candidate: list[str]) -> dict[str, int]:
    # difflib is linear-space and handles full-episode word streams.  A
    # quadratic edit matrix would need hundreds of MiB for a 12k-word SRT.
    out = {"insertions": 0, "deletions": 0, "substitutions": 0}
    for kind, a0, a1, b0, b1 in difflib.SequenceMatcher(None, reference, candidate,
                                                          autojunk=False).get_opcodes():
        if kind == "insert": out["insertions"] += b1 - b0
        elif kind == "delete": out["deletions"] += a1 - a0
        elif kind == "replace":
            common = min(a1 - a0, b1 - b0)
            out["substitutions"] += common
            out["deletions"] += (a1 - a0) - common
            out["insertions"] += (b1 - b0) - common
    return out


def structural(cues: list) -> dict:
    durations = [cue.end - cue.start for cue in cues]
    overlaps = sum(1 for a, b in zip(cues, cues[1:]) if b.start < a.end - 1e-6)
    return {"cue_count": len(cues), "average_duration": statistics.mean(durations) if durations else 0,
            "median_duration": statistics.median(durations) if durations else 0,
            "min_duration": min(durations, default=0), "max_duration": max(durations, default=0),
            "short_cue_count": sum(d < MIN_CUE_DURATION for d in durations),
            "overlap_count": overlaps}


def readability(cues: list) -> dict:
    return {"cues_over_max_cps": sum(c.end > c.start and len(c.text) / (c.end - c.start) > MAX_CPS for c in cues),
            "cues_over_max_chars": sum(any(len(line) > MAX_CHARS_PER_LINE for line in getattr(c, "lines", [c.text])) for c in cues),
            "cues_over_max_duration": sum(c.end - c.start > MAX_CUE_DURATION for c in cues),
            "cues_under_min_duration": sum(c.end - c.start < MIN_CUE_DURATION for c in cues)}


MIN_ANCHOR_WORDS = 3


def _timed_words(cues: list) -> tuple[list[str], list[float]]:
    """Every word with an interpolated time (its position inside its cue)."""
    tokens, times = [], []
    for cue in cues:
        cue_words = words(cue.text)
        for k, word in enumerate(cue_words):
            tokens.append(word)
            times.append(cue.start + (cue.end - cue.start) * (k + 0.5) / len(cue_words))
    return tokens, times


def _anchor_offsets(reference: list, candidate: list) -> list[tuple[float, float]]:
    """(candidate time - reference time, candidate time) for every word in
    a run of >= MIN_ANCHOR_WORDS identical words the two transcripts share.

    Aligning on words rather than cues keeps the measurement honest when the
    two files are segmented differently: pairing by time overlap picks the
    wrong neighbour as soon as the shift exceeds a cue's length (a 5 s shift
    against 3 s cues), and pairing by whole-cue text fails once either side
    splits or merges cues."""
    ref_words, ref_times = _timed_words(reference)
    cand_words, cand_times = _timed_words(candidate)
    matcher = difflib.SequenceMatcher(None, ref_words, cand_words, autojunk=False)
    offsets = []
    for block in matcher.get_matching_blocks():
        if block.size < MIN_ANCHOR_WORDS:
            continue
        for k in range(block.size):
            cand_time = cand_times[block.b + k]
            offsets.append((cand_time - ref_times[block.a + k], cand_time))
    return offsets


def _cue_offsets(reference: list, candidate: list) -> list[tuple[float, float]]:
    # Fallback for transcripts too different to anchor on words (e.g. very
    # short cues). Pair only overlapping cues; midpoints measure
    # origin/timeline shifts without requiring identical segmentation.
    offsets = []
    for cue in candidate:
        # Text equality is the strongest pairing signal and remains useful
        # when a global offset means the timelines do not overlap at all.
        cue_words = words(cue.text)
        matches = [ref for ref in reference if words(ref.text) == cue_words and cue_words]
        if not matches:
            matches = [ref for ref in reference if min(ref.end, cue.end) > max(ref.start, cue.start)]
        if matches:
            nearest = min(matches, key=lambda ref: abs((ref.start + ref.end - cue.start - cue.end) / 2))
            offsets.append(((cue.start + cue.end - nearest.start - nearest.end) / 2, (cue.start + cue.end) / 2))
    return offsets


def timing(reference: list, candidate: list) -> dict:
    offsets = _anchor_offsets(reference, candidate)
    pairing = "words"
    if len(offsets) < 2:
        offsets, pairing = _cue_offsets(reference, candidate), "cues"
    if len(offsets) < 2:
        return {"matched_cues": len(offsets), "pairing": pairing, "mean_time_offset": None, "median_time_offset": None,
                "p95_time_offset": None, "estimated_drift": None, "classification": "insufficient_matches"}
    values = [x[0] for x in offsets]
    mean_x = statistics.mean(x[1] for x in offsets); mean_y = statistics.mean(values)
    denominator = sum((x - mean_x) ** 2 for _, x in offsets)
    slope = sum((x - mean_x) * (y - mean_y) for y, x in offsets) / denominator if denominator else 0.0
    residuals = [y - (mean_y + slope * (x - mean_x)) for y, x in offsets]
    p95 = sorted(values)[int(.95 * (len(values) - 1))]
    spread = statistics.pstdev(values)
    classification = "constant_offset" if abs(slope) < 0.001 and spread < 0.25 else "drift_or_nonlinear"
    return {"matched_cues": len(offsets), "pairing": pairing, "mean_time_offset": statistics.mean(values),
            "median_time_offset": statistics.median(values), "p95_time_offset": p95,
            "estimated_drift": slope, "residual_p95": sorted(abs(x) for x in residuals)[int(.95 * (len(residuals) - 1))],
            "classification": classification}


def evaluate(reference_path: str | Path, candidate_path: str | Path) -> dict:
    reference, candidate = srt.parse(reference_path), srt.parse(candidate_path)
    ref_words, candidate_words = words(" ".join(c.text for c in reference)), words(" ".join(c.text for c in candidate))
    edits = edit_counts(ref_words, candidate_words)
    return {"reference": structural(reference), "candidate": structural(candidate),
            "text": {"reference_word_count": len(ref_words), "candidate_word_count": len(candidate_words),
                     "normalized_word_match": max(0.0, 1 - sum(edits.values()) / max(len(ref_words), 1)), **edits},
            "timing": timing(reference, candidate), "readability": readability(candidate)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference"); parser.add_argument("candidate")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()
    result = evaluate(args.reference, args.candidate)
    payload = json.dumps(result, indent=2, ensure_ascii=False)
    print(payload)
    if args.json_path: Path(args.json_path).write_text(payload + "\n", encoding="utf-8")


if __name__ == "__main__": main()
