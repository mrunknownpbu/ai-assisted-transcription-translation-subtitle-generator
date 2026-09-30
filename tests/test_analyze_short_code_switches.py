"""Review-only short code-switch candidate analysis."""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

_spec = importlib.util.spec_from_file_location(
    "analyze_short_code_switches",
    Path(__file__).resolve().parent.parent / "scripts" / "analyze_short_code_switches.py")
analysis = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(analysis)


class CandidateRunTests(unittest.TestCase):
    def test_two_distinct_foreign_clauses_are_reported_for_review(self):
        detections = iter([("es", 0.99), ("es", 0.98)])
        with patch.object(analysis, "detect_text_language", side_effect=lambda _: next(detections)):
            runs = analysis.candidate_runs(
                ["Este hombre es increíble.", "Mire el estado de esta casa."], "tr")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["language"], "es")
        self.assertEqual(runs[0]["sentence_indices"], [0, 1])
        self.assertFalse(runs[0]["repeated_text_only"])

    def test_repeated_text_is_explicitly_marked_as_a_false_positive_risk(self):
        detections = iter([("de", 0.99), ("de", 0.99)])
        with patch.object(analysis, "detect_text_language", side_effect=lambda _: next(detections)):
            runs = analysis.candidate_runs(["Bu tekrar eden yabancı cümledir.",
                                            "Bu tekrar eden yabancı cümledir."], "tr",
                                           min_chars=1)
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0]["repeated_text_only"])
        self.assertEqual(runs[0]["distinct_clause_count"], 1)

    def test_short_clause_does_not_break_candidate_run(self):
        detections = iter([("es", 0.99), ("es", 0.98)])
        with patch.object(analysis, "detect_text_language", side_effect=lambda _: next(detections)):
            runs = analysis.candidate_runs(
                ["Este hombre es increíble.", "Sí.", "Mire el estado de esta casa."], "tr")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["sentence_indices"], [0, 2])

    def test_source_language_clause_breaks_a_candidate_run(self):
        detections = iter([("es", 0.99), ("tr", 0.99), ("es", 0.98)])
        with patch.object(analysis, "detect_text_language", side_effect=lambda _: next(detections)):
            runs = analysis.candidate_runs(
                ["Este hombre es increíble.", "Bu yeterince uzun normal bir Türkçe cümledir.",
                 "Mire el estado de esta casa."], "tr")
        self.assertEqual(runs, [])


if __name__ == "__main__":
    unittest.main()
