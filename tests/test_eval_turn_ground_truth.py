"""Deterministic tests for scripts/eval_turn_ground_truth.py."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

from transcript import Word

_spec = importlib.util.spec_from_file_location(
    "eval_turn_ground_truth",
    Path(__file__).resolve().parent.parent / "scripts" / "eval_turn_ground_truth.py",
)
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


def word(start: float) -> Word:
    return Word(text="word", original_text="word", start=start, end=start + 0.1)


class AnnotationFormatTests(unittest.TestCase):
    def test_comments_and_inline_comments_are_ignored(self):
        with patch.object(Path, "read_text", return_value="# turn boundaries\n2.5 # first speaker change\n\n1.0\n"):
            self.assertEqual(ev.load_annotations("episode.turns"), [1.0, 2.5])

    def test_invalid_or_duplicate_timestamp_is_rejected(self):
        with patch.object(Path, "read_text", return_value="1.0\n1.0\n"):
            with self.assertRaisesRegex(ValueError, "duplicate"):
                ev.load_annotations("episode.turns")
        with patch.object(Path, "read_text", return_value="-1\n"):
            with self.assertRaisesRegex(ValueError, "non-negative"):
                ev.load_annotations("episode.turns")


class TurnScoringTests(unittest.TestCase):
    def test_score_uses_detected_index_start_times_and_one_to_one_matching(self):
        words = [word(0.0), word(1.02), word(1.15), word(3.0)]
        with patch.object(ev.turns, "detect_turns", return_value={1, 2, 3}) as detect:
            result = ev.score_turns(words, [1.0, 3.15], tolerance=0.2, mode="heuristic")
        detect.assert_called_once_with(words, None, mode="heuristic")
        self.assertEqual(result["status"], "scored")
        self.assertEqual((result["matched_turns"], result["annotated_turns"], result["detected_turns"]),
                         (2, 2, 3))
        self.assertAlmostEqual(result["precision"], 2 / 3)
        self.assertEqual(result["recall"], 1.0)
        self.assertAlmostEqual(result["f1"], 0.8)

    def test_empty_annotations_are_not_scored_as_zero_turn_ground_truth(self):
        with patch.object(ev.turns, "detect_turns", return_value={1}):
            result = ev.score_turns([word(0.0), word(1.0)], [], tolerance=0.35)
        self.assertEqual(result["status"], "no_ground_truth")
        self.assertEqual(result["detected_turns"], 1)
        self.assertEqual(result["matched_turns"], 0)
        self.assertIsNone(result["precision"])
        self.assertIsNone(result["recall"])
        self.assertIsNone(result["f1"])

    def test_empty_detection_against_annotations_has_zero_metrics(self):
        with patch.object(ev.turns, "detect_turns", return_value=set()):
            result = ev.score_turns([], [1.0], tolerance=0.35)
        self.assertEqual((result["precision"], result["recall"], result["f1"]), (0.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
