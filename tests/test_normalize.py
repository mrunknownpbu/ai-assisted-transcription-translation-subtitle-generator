import re
import unittest
from unittest.mock import patch

import normalize
from normalize import normalize_word
from transcript import CorrectionKind, Word


def word(text):
    return Word(text=text, original_text=text, start=0.0, end=1.0)


class PetunyaCorrectionTests(unittest.TestCase):
    def test_corrects_and_preserves_original(self):
        w = normalize_word(word("Petunia"), "tr")
        self.assertEqual(w.text, "Petunya")
        self.assertEqual(w.original_text, "Petunia")

    def test_case_preserving_lowercase(self):
        self.assertEqual(normalize_word(word("petunia"), "tr").text, "petunya")

    def test_case_preserving_uppercase(self):
        self.assertEqual(normalize_word(word("PETUNIA"), "tr").text, "PETUNYA")

    def test_whole_word_only(self):
        w = word("Petunian")
        result = normalize_word(w, "tr")
        self.assertEqual(result.text, "Petunian")
        self.assertEqual(result.corrections, [])

    def test_records_provenance(self):
        w = normalize_word(word("Petunia"), "tr")
        self.assertEqual(len(w.corrections), 1)
        self.assertEqual(w.corrections[0].kind, CorrectionKind.NORMALIZATION)
        self.assertEqual(w.corrections[0].rule_id, "tr-petunia-petunya")
        self.assertEqual(w.corrections[0].confidence, 1.0)

    def test_scoped_to_turkish_only(self):
        w = normalize_word(word("Petunia"), "en")
        self.assertEqual(w.text, "Petunia")
        self.assertEqual(w.corrections, [])

    def test_unrelated_word_untouched(self):
        w = normalize_word(word("Serkan"), "tr")
        self.assertEqual(w.text, "Serkan")
        self.assertEqual(w.corrections, [])


class ConfidenceThresholdTests(unittest.TestCase):
    """MIN_NORMALIZATION_CONFIDENCE (added 2026-09-19): a rule below
    threshold is a candidate, not a decision -- the word must be
    preserved unchanged, per "if confidence is insufficient, preserve
    the original ASR output." No real rule in _RULES is below the
    threshold today (every rule requires independent confirmation, per
    this module's own docstring), so this uses a synthetic test-only
    rule to exercise the gate mechanism itself in isolation."""

    def _rules_with(self, confidence):
        return {
            "test-low-confidence": (
                re.compile(r"\bfoo\b", re.IGNORECASE), "bar",
                "synthetic test rule", confidence,
            ),
        }

    def test_below_threshold_rule_leaves_word_unchanged(self):
        with patch.object(normalize, "_RULES", self._rules_with(0.5)):
            w = normalize_word(word("foo"), "tr")
        self.assertEqual(w.text, "foo")
        self.assertEqual(w.corrections, [])

    def test_at_or_above_threshold_rule_applies(self):
        with patch.object(normalize, "_RULES", self._rules_with(0.85)):
            w = normalize_word(word("foo"), "tr")
        self.assertEqual(w.text, "bar")
        self.assertEqual(len(w.corrections), 1)

    def test_existing_petunya_rule_confidence_clears_the_threshold(self):
        pattern, replacement, evidence, confidence = normalize._RULES["tr-petunia-petunya"]
        self.assertGreaterEqual(confidence, normalize.MIN_NORMALIZATION_CONFIDENCE)


if __name__ == "__main__":
    unittest.main()
