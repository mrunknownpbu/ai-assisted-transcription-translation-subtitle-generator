import unittest

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


if __name__ == "__main__":
    unittest.main()
