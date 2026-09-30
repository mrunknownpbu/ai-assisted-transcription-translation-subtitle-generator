#!/usr/bin/env python3
"""Score speaker-turn detection against hand-annotated turn timestamps.

The ground-truth file is deliberately a compact, line-oriented text format:
one timestamp in seconds per speaker-turn boundary. Blank lines and lines
starting with ``#`` are ignored; an inline ``#`` comment is also allowed::

    # New speaker starts at the first word after this boundary.
    12.450  # A -> B
    18.000

The transcript is the project's canonical transcript JSON (the cached format
written by ``CanonicalTranscript.save``). Its non-suppressed, normalized words
are passed unchanged to ``turns.detect_turns``; a detected index is scored at
the start time of its first word. A prediction and annotation match once, at
most, when their timestamps differ by no more than ``--tolerance`` seconds.

Run from the repository or app container (no GPU required for heuristic mode):

    python scripts/eval_turn_ground_truth.py \
        --transcript /cache/transcripts/episode.json \
        --ground-truth annotations/episode.turns \
        --mode heuristic --tolerance 0.35

Use ``--mode voice --wav episode.wav`` to evaluate the voice detector. Omit
``--mode`` to use the application's configured default. An empty annotation
file means no ground truth, not a zero-turn reference: the command succeeds
and reports ``status=no_ground_truth`` with precision/recall/F1 as null.
``--json`` writes the same structured result for aggregation.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "subtitle_ai"))

import hallucination  # noqa: E402
import normalize  # noqa: E402
import turns  # noqa: E402
from transcript import CanonicalTranscript, Word  # noqa: E402


def load_annotations(path: str | Path) -> list[float]:
    """Read sorted unique timestamps from the human-editable text format."""
    timestamps: list[float] = []
    for line_number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        value = raw.split("#", 1)[0].strip()
        if not value:
            continue
        try:
            timestamp = float(value)
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: expected one timestamp in seconds") from exc
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError(f"{path}:{line_number}: timestamp must be finite and non-negative")
        timestamps.append(timestamp)
    timestamps.sort()
    if any(left == right for left, right in zip(timestamps, timestamps[1:])):
        raise ValueError(f"{path}: duplicate timestamp")
    return timestamps


def production_words(transcript_path: str | Path) -> list[Word]:
    """Recreate the normalized, non-suppressed word list used by the pipeline."""
    data = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
    transcript = CanonicalTranscript.from_dict(data)
    hallucination.detect(transcript.segments, transcript.language)
    return [
        word
        for segment in transcript.segments
        if not segment.suppressed
        for word in normalize.normalize_transcript_words(list(segment.words), transcript.language)
    ]


def match_points(reference: list[float], predicted: list[float],
                 tolerance: float) -> tuple[int, int, int]:
    """One-to-one greedy nearest timestamp matching, as in eval_transcription."""
    predicted = sorted(predicted)
    used = [False] * len(predicted)
    matches = 0
    for target in sorted(reference):
        best, best_distance = None, tolerance + 1e-9
        for index, candidate in enumerate(predicted):
            if used[index]:
                continue
            distance = abs(candidate - target)
            if distance <= tolerance and distance < best_distance:
                best, best_distance = index, distance
        if best is not None:
            used[best] = True
            matches += 1
    return matches, len(reference), len(predicted)


def score_turns(words: list[Word], reference: list[float], *, tolerance: float,
                mode: str | None = None, wav_path: str | None = None) -> dict:
    """Run the existing detector and return counts plus precision/recall/F1."""
    indices = turns.detect_turns(words, wav_path, mode=mode)
    predicted = [words[index].start for index in sorted(indices) if 0 <= index < len(words)]
    result = {
        "status": "scored" if reference else "no_ground_truth",
        "tolerance_seconds": tolerance,
        "annotated_turns": len(reference),
        "detected_turns": len(predicted),
        "matched_turns": 0,
        "precision": None,
        "recall": None,
        "f1": None,
    }
    if not reference:
        return result
    matched, reference_count, predicted_count = match_points(reference, predicted, tolerance)
    precision = matched / predicted_count if predicted_count else 0.0
    recall = matched / reference_count
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    result.update(matched_turns=matched, precision=precision, recall=recall, f1=f1)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--transcript", required=True, help="canonical transcript JSON")
    parser.add_argument("--ground-truth", required=True, help="one timestamp per line")
    parser.add_argument("--mode", choices=("heuristic", "voice", "off"),
                        help="detector mode (default: application's configured mode)")
    parser.add_argument("--wav", help="WAV input for --mode voice")
    parser.add_argument("--tolerance", type=float, default=0.35,
                        help="maximum timestamp difference in seconds (default: 0.35)")
    parser.add_argument("--json", help="write structured result to this path")
    args = parser.parse_args()
    if not math.isfinite(args.tolerance) or args.tolerance < 0:
        parser.error("--tolerance must be finite and non-negative")

    try:
        reference = load_annotations(args.ground_truth)
        result = score_turns(production_words(args.transcript), reference,
                             tolerance=args.tolerance, mode=args.mode, wav_path=args.wav)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(f"status={result['status']} annotations={result['annotated_turns']} "
          f"detected={result['detected_turns']} matched={result['matched_turns']}")
    if result["status"] == "scored":
        print(f"precision={result['precision']:.3f} recall={result['recall']:.3f} "
              f"f1={result['f1']:.3f} tolerance={result['tolerance_seconds']:.3f}s")
    else:
        print("precision=NA recall=NA f1=NA (empty annotation file is not a zero-turn reference)")
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
