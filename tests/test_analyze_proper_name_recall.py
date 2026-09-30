"""Deterministic unit tests for the measurement-only name-recall analyzer."""

import importlib.util
import unittest
from pathlib import Path

from srt import SrtCue

_spec = importlib.util.spec_from_file_location(
    "analyze_proper_name_recall",
    Path(__file__).resolve().parent.parent / "scripts" / "analyze_proper_name_recall.py")
analysis = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(analysis)


class NameFormLoadingTests(unittest.TestCase):
    def test_glossary_canonical_and_surface_forms_are_loaded_once(self):
        forms = analysis.forms_from_glossary_data({
            "entities": [{"canonical": "Ayşe", "surface_forms": ["Ayşe", "Ayşe'yi"]}],
        })
        self.assertEqual(forms, [
            analysis.NameForm("Ayşe", "Ayşe"),
            analysis.NameForm("Ayşe", "Ayşe'yi"),
        ])

    def test_name_file_lines_ignore_comments_and_whitespace(self):
        self.assertEqual(
            analysis.forms_from_name_lines(["  Ada Lovelace ", "", "# note", "Ada Lovelace"]),
            [analysis.NameForm("Ada Lovelace", "Ada Lovelace")],
        )


class ProperNameRecallTests(unittest.TestCase):
    def setUp(self):
        self.forms = [
            analysis.NameForm("Ayşe", "Ayşe"),
            analysis.NameForm("Ayşe", "Ayşe'yi"),
            analysis.NameForm("Mehmet", "Mehmet"),
        ]

    def test_exact_recall_and_missed_form_are_reported(self):
        production = [SrtCue(0, 2, "Ayşe geldi."), SrtCue(3, 5, "Mehmet bekliyor.")]
        reference = [SrtCue(0, 2, "Ayşe geldi."), SrtCue(3, 5, "Ayşe'yi gördüm.")]
        report = analysis.analyze_cues(production, reference, self.forms)
        self.assertEqual(report["summary"]["exact_recall"], 0.5)
        self.assertEqual(report["forms"][0]["exact_hits"], 1)
        self.assertEqual(report["forms"][1]["misses"], 1)
        self.assertEqual(report["missed_forms"][0]["form"], "Ayşe'yi")

    def test_same_entity_alias_is_an_exact_form_mismatch(self):
        production = [SrtCue(0, 2, "Ayşe geldi.")]
        reference = [SrtCue(0, 2, "Ayşe'yi gördüm.")]
        report = analysis.analyze_cues(production, reference, self.forms)
        row = report["missed_forms"][0]
        self.assertEqual(row["exact_form_mismatches"], 1)
        self.assertEqual(report["evidence"][0]["status"], "exact_form_mismatch")

    def test_no_overlapping_production_cue_is_absent_content(self):
        production = [SrtCue(3, 5, "Mehmet bekliyor.")]
        reference = [SrtCue(0, 2, "Mehmet geldi.")]
        report = analysis.analyze_cues(production, reference, self.forms)
        self.assertEqual(report["summary"]["absent_content"], 1)
        self.assertEqual(report["evidence"][0]["status"], "absent_content")

    def test_overlapping_non_name_content_is_distinguished_from_absence(self):
        production = [SrtCue(0, 2, "Birisi geldi.")]
        reference = [SrtCue(0, 2, "Mehmet geldi.")]
        report = analysis.analyze_cues(production, reference, self.forms)
        self.assertEqual(report["summary"]["name_absent_in_production_content"], 1)
        self.assertEqual(report["evidence"][0]["status"], "name_absent_in_production_content")

    def test_latin_name_does_not_match_inside_a_larger_word(self):
        forms = [analysis.NameForm("Can", "Can")]
        report = analysis.analyze_cues([SrtCue(0, 1, "Cankaya")],
                                       [SrtCue(0, 1, "Can geldi.")], forms)
        self.assertEqual(report["summary"]["exact_hits"], 0)
        self.assertEqual(report["summary"]["name_absent_in_production_content"], 1)


if __name__ == "__main__":
    unittest.main()
