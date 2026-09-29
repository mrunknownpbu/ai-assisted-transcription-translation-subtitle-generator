"""text_segmentation.py: the shared, language-parameterized sentence/
clause-splitting algorithm ported from segmentation_target.py so
segmentation_source.py can use the same "best-scoring boundary near the
midpoint" design for source-language (e.g. Turkish) text. English-lexicon
behaviour mirrors tests/test_segmentation_target.py by construction; this
file's own job is the Turkish lexicon and the ENGLISH-default passthrough."""

import unittest

from text_segmentation import TURKISH, split_long_piece, split_sentences, wrap_lines


class EnglishDefaultMatchesSegmentationTargetTests(unittest.TestCase):
    """Same inputs/outputs as segmentation_target.py's own tests -- the
    ENGLISH lexicon must reproduce its already-validated behaviour."""

    def test_splits_on_sentence_punctuation(self):
        self.assertEqual(split_sentences("Eda, Eda, my daughter, wake up! I woke up."),
                         ["Eda, Eda, my daughter, wake up!", "I woke up."])

    def test_splits_japanese_sentence_marks_without_spaces_and_keeps_closers(self):
        self.assertEqual(split_sentences("「こんにちは。」次です！本当？』"),
                         ["「こんにちは。」", "次です！", "本当？』"])

    def test_title_abbreviation_period_is_not_a_sentence_end(self):
        self.assertEqual(split_sentences("I saw Dr. Smith today. He left."),
                         ["I saw Dr. Smith today.", "He left."])

    def test_long_sentence_splits_at_clause_boundary(self):
        text = "Wait, I need a minute, please, because I forgot my keys at home again today."
        parts = split_long_piece(text, 40)
        self.assertTrue(all(len(p) <= 40 for p in parts))
        self.assertEqual(" ".join(parts), text)


class TurkishLexiconTests(unittest.TestCase):
    def test_title_abbreviation_period_is_not_a_sentence_end(self):
        self.assertEqual(split_sentences("Dr. Serkan geldi. Eda gitti.", TURKISH),
                         ["Dr. Serkan geldi.", "Eda gitti."])

    def test_ordinary_period_still_splits(self):
        self.assertEqual(split_sentences("Serkan geldi. Eda gitti.", TURKISH),
                         ["Serkan geldi.", "Eda gitti."])

    def test_long_sentence_prefers_conjunction_boundary(self):
        # "ama" (but) is a TURKISH strong conjunction and should be
        # preferred as a split point, the way "but"/"because" are for
        # English in segmentation_target's own tests.
        text = "Sana bunu söylemek istemiyordum ama artık gerçeği bilmen gerekiyor bence."
        parts = split_long_piece(text, 45, TURKISH)
        self.assertTrue(all(len(p) <= 45 for p in parts))
        self.assertEqual(" ".join(parts), text)
        self.assertTrue(any(p.split()[0] == "ama" for p in parts[1:]))

    def test_never_loses_or_duplicates_words(self):
        text = "Bu tek kısa kaynak replik nedense inanılmaz derecede uzun bir çeviri aldı bugün."
        parts = split_long_piece(text, 42, TURKISH)
        self.assertEqual(" ".join(parts).split(), text.split())

    def test_wrap_lines_respects_max_width(self):
        text = "Serkan ile Eda bugün sahile gidip uzun uzun konuştular ve çok güldüler"
        lines = wrap_lines(text, 42, TURKISH)
        self.assertTrue(all(len(l) <= 42 for l in lines))
        self.assertEqual(" ".join(lines).split(), text.split())

    def test_no_clingy_word_list_means_no_lexical_penalty(self):
        # TURKISH.clingy_words is deliberately empty -- a word like "bir"
        # must not be penalised as a line-ender just by being present.
        from text_segmentation import _boundary_score
        self.assertEqual(_boundary_score("bir", "ev", TURKISH), 0.0)

    def test_japanese_comma_is_a_clause_boundary(self):
        from text_segmentation import SplitLexicon, _boundary_score
        self.assertEqual(_boundary_score("前半、", "後半", SplitLexicon()), 2.0)

    def test_single_unspaced_token_longer_than_max_returns_it_unsplit(self):
        # Real production crash this fixes (2026-09-28, Hammer Session!
        # S01E01 via segmentation_source.build_cues -> wrap_lines):
        # "ValueError: min() iterable argument is empty" when text.split()
        # produces 0 or 1 words -- range(1, len(words)) is then empty.
        text = "a" * 60
        self.assertEqual(wrap_lines(text), [text])
        self.assertEqual(wrap_lines(text, lexicon=TURKISH), [text])


if __name__ == "__main__":
    unittest.main()
