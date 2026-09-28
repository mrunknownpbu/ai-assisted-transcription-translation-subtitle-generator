"""pipeline._distribute_span_text/_pack_pieces_by_weight: replaces the old
raw word-count-FRACTION cut (could land mid-sentence, always discarded a
translation span's own newline/dash structure via plain .split()) with a
sentence- or dash-line-aware distribution across the display groups a
translation span covers. Natural-dialogue plan step 4."""

from __future__ import annotations

import unittest

from pipeline import _distribute_span_text, _pack_pieces_by_weight


class SingleGroupTests(unittest.TestCase):
    def test_single_weight_returns_text_unchanged(self):
        self.assertEqual(_distribute_span_text("Whatever this text is.", [3]), ["Whatever this text is."])


class DashLineDistributionTests(unittest.TestCase):
    def test_two_speaker_dash_text_splits_one_line_per_group(self):
        text = "- Good morning.\n- Good morning to you too."
        self.assertEqual(_distribute_span_text(text, [2, 5]),
                         ["- Good morning.", "- Good morning to you too."])

    def test_dash_line_count_mismatch_falls_back_to_sentence_packing(self):
        # 3 dash lines but only 2 groups -- doesn't match 1:1, falls
        # through to ordinary sentence-based packing of the whole text.
        text = "- One.\n- Two.\n- Three."
        result = _distribute_span_text(text, [1, 1])
        self.assertEqual(len(result), 2)
        self.assertEqual(" ".join(result), text.replace("\n", " "))


class SentenceDistributionTests(unittest.TestCase):
    def test_never_splits_a_sentence_in_half(self):
        text = "I woke up early today. Then I went to work. It was a long day."
        result = _distribute_span_text(text, [1, 1, 1])
        self.assertEqual(len(result), 3)
        for piece in result:
            self.assertTrue(piece.strip())
        # every sentence survives exactly once, none truncated mid-way
        self.assertEqual(" ".join(result), text)

    def test_weights_bias_which_group_gets_more_sentences(self):
        text = "One. Two. Three. Four."
        heavy_first = _distribute_span_text(text, [3, 1])
        self.assertTrue(heavy_first[0].startswith("One"))
        self.assertGreater(len(heavy_first[0]), len(heavy_first[1]))

    def test_fewer_sentences_than_groups_splits_the_shared_one_by_words_not_duplicated(self):
        # Real regression this guards (caught on a real S01E01 run): an
        # earlier version back-filled every bucket that got no sentence
        # of its own with the FULL text, producing visible duplicate
        # consecutive cues. There are enough words to go around here, so
        # no bucket should ever be empty OR get the whole sentence twice.
        text = "Just one sentence here."
        result = _distribute_span_text(text, [1, 1, 1])
        self.assertEqual(len(result), 3)
        self.assertTrue(all(p.strip() for p in result))
        self.assertEqual(" ".join(result).split(), text.split())   # no word lost or duplicated

    def test_pathological_fewer_words_than_groups_is_the_only_duplicate_case(self):
        text = "No."
        result = _distribute_span_text(text, [1, 1, 1])
        self.assertEqual(result, [text, text, text])


class PackPiecesByWeightTests(unittest.TestCase):
    def test_equal_weights_split_evenly(self):
        pieces = ["a.", "b.", "c.", "d."]
        result = _pack_pieces_by_weight(pieces, [1, 1])
        self.assertEqual(result, ["a. b.", "c. d."])

    def test_last_bucket_absorbs_remainder(self):
        pieces = ["a.", "b.", "c."]
        result = _pack_pieces_by_weight(pieces, [1, 1])
        self.assertEqual(" ".join(result).split(), ["a.", "b.", "c."])


if __name__ == "__main__":
    unittest.main()
