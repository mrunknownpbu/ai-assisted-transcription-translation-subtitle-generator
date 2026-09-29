"""pipeline._distribute_span_text/_pack_pieces_by_weight: replaces the old
raw word-count-FRACTION cut (could land mid-sentence, always discarded a
translation span's own newline/dash structure via plain .split()) with a
sentence- or dash-line-aware distribution across the display groups a
translation span covers. Natural-dialogue plan step 4.

_distribute_span_text returns runs `(first, stop, text)` over group
positions; a run covering 2+ groups is shown as one display window. Fewer
English sentences than groups merges groups instead of cutting a sentence
at the word level (2026-09-29, Hammer Session! S01E01)."""

from __future__ import annotations

import unittest

from pipeline import _distribute_span_text, _group_weight, _pack_pieces_by_weight
from transcript import Segment, Word


def _texts(runs):
    return [text for _, _, text in runs]


class SingleGroupTests(unittest.TestCase):
    def test_single_weight_returns_text_unchanged(self):
        self.assertEqual(_distribute_span_text("Whatever this text is.", [3]), [(0, 1, "Whatever this text is.")])


class DashLineDistributionTests(unittest.TestCase):
    def test_two_speaker_dash_text_splits_one_line_per_group(self):
        text = "- Good morning.\n- Good morning to you too."
        self.assertEqual(_distribute_span_text(text, [2, 5]),
                         [(0, 1, "- Good morning."), (1, 2, "- Good morning to you too.")])

    def test_dash_line_count_mismatch_falls_back_to_sentence_packing(self):
        # 3 dash lines but only 2 groups -- doesn't match 1:1, falls
        # through to ordinary sentence-based packing of the whole text.
        text = "- One.\n- Two.\n- Three."
        result = _texts(_distribute_span_text(text, [1, 1]))
        self.assertEqual(len(result), 2)
        self.assertEqual(" ".join(result), text.replace("\n", " "))


class SentenceDistributionTests(unittest.TestCase):
    def test_never_splits_a_sentence_in_half(self):
        text = "I woke up early today. Then I went to work. It was a long day."
        result = _texts(_distribute_span_text(text, [1, 1, 1]))
        self.assertEqual(len(result), 3)
        for piece in result:
            self.assertTrue(piece.strip())
        # every sentence survives exactly once, none truncated mid-way
        self.assertEqual(" ".join(result), text)

    def test_weights_bias_which_group_gets_more_sentences(self):
        text = "One. Two. Three. Four."
        heavy_first = _texts(_distribute_span_text(text, [3, 1]))
        self.assertTrue(heavy_first[0].startswith("One"))
        self.assertGreater(len(heavy_first[0]), len(heavy_first[1]))

    def test_real_one_sentence_over_three_groups_becomes_one_run(self):
        # Hammer Session! S01E01 1881.2-1895.6s: three Japanese groups (equal
        # weight -- see _group_weight) and one English sentence were shown
        # as "Hey, can" / "you" / "read it?".
        self.assertEqual(_distribute_span_text("Hey, can you read it?", [1, 1, 1]),
                         [(0, 3, "Hey, can you read it?")])

    def test_real_one_sentence_over_two_groups_becomes_one_run(self):
        # S01E01 3137.2-3147.9s: "Oh," / "my god." as two cues.
        self.assertEqual(_distribute_span_text("Oh, my god.", [3, 5]), [(0, 2, "Oh, my god.")])

    def test_fewer_sentences_than_groups_never_splits_or_duplicates_a_sentence(self):
        text = "One. Two. Three."
        runs = _distribute_span_text(text, [1, 1, 1, 1, 1])
        self.assertEqual(_texts(runs), ["One.", "Two.", "Three."])
        # runs are contiguous and cover every group exactly once
        self.assertEqual(runs[0][0], 0)
        self.assertEqual(runs[-1][1], 5)
        for (_, stop, _), (first, _, _) in zip(runs, runs[1:]):
            self.assertEqual(stop, first)
        self.assertTrue(all(stop > first for first, stop, _ in runs))

    def test_merge_follows_where_the_sentences_fall(self):
        # A short first sentence over a light first group, a long second
        # sentence over the two heavier groups after it.
        runs = _distribute_span_text("Short. And a much longer second sentence here.", [2, 2, 9])
        self.assertEqual(runs, [(0, 1, "Short."), (1, 3, "And a much longer second sentence here.")])

    def test_enough_sentences_still_one_run_per_group(self):
        runs = _distribute_span_text("One. Two. Three. Four.", [1, 1])
        self.assertEqual([(f, s) for f, s, _ in runs], [(0, 1), (1, 2)])


def _cue(text, language):
    return Segment(index=0, start=0.0, end=1.0, words=[Word(text=text, original_text=text, start=0.0, end=1.0)],
                   avg_logprob=0.0, no_speech_prob=0.0, compression_ratio=0.0, language=language)


class GroupWeightTests(unittest.TestCase):
    def test_unspaced_language_weighs_by_characters_not_one_word_per_cue(self):
        short = _group_weight([_cue("\u304f\u305d", "ja")], "ja")
        long = _group_weight([_cue("\u304a\u524d\u30b1\u30f3\u30ab\u58f2\u3063\u3066\u3093\u306e\u304b", "ja")], "ja")
        self.assertEqual((short, long), (2, 11))

    def test_spaced_language_still_counts_words(self):
        self.assertEqual(_group_weight([_cue("Bir iki \u00fc\u00e7", "tr")], "tr"), 3)


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
