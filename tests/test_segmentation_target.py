import unittest

from segmentation_target import (distribute_timing, segment, split_long_piece,
                                 split_sentences, wrap_lines)


class SplitSentencesTests(unittest.TestCase):
    def test_splits_on_sentence_punctuation(self):
        self.assertEqual(split_sentences("Eda, Eda, my daughter, wake up! I woke up."),
                         ["Eda, Eda, my daughter, wake up!", "I woke up."])

    def test_single_sentence_no_split(self):
        self.assertEqual(split_sentences("Hello world"), ["Hello world"])

    def test_empty_text(self):
        self.assertEqual(split_sentences(""), [])


class SplitLongPieceTests(unittest.TestCase):
    def test_short_piece_unchanged(self):
        self.assertEqual(split_long_piece("Hello world.", 84), ["Hello world."])

    def test_long_sentence_splits_at_clause_boundary(self):
        text = "Wait, I need a minute, please, because I forgot my keys at home again today."
        parts = split_long_piece(text, 40)
        self.assertTrue(all(len(p) <= 40 for p in parts))
        self.assertEqual(" ".join(parts), text)

    def test_never_loses_or_duplicates_words(self):
        text = "This single short source cue somehow received an implausibly long English translation."
        parts = split_long_piece(text, 42)
        self.assertEqual(" ".join(parts).split(), text.split())


class DistributeTimingTests(unittest.TestCase):
    def test_single_piece_gets_whole_envelope(self):
        self.assertEqual(distribute_timing(["one"], 10.0, 20.0), [(10.0, 20.0)])

    def test_timing_is_monotonic_and_fills_envelope(self):
        timings = distribute_timing(["a", "bb", "ccc"], 0.0, 9.0)
        self.assertEqual(timings[0][0], 0.0)
        self.assertEqual(timings[-1][1], 9.0)
        for (s1, e1), (s2, e2) in zip(timings, timings[1:]):
            self.assertLessEqual(e1, s2 + 1e-9)

    def test_reading_time_not_word_count_drives_the_split(self):
        # Piece A: one very long word. Piece B: many short words but
        # overall shorter text. Word-count-proportional timing (the old
        # architecture) would give B more time for having more "words";
        # reading-time timing must give A more time because it has more
        # characters to read.
        long_word_piece = "Supercalifragilisticexpialidocious"     # 35 chars, 1 word
        many_short_words = "a a a a a a a a a a"                    # 19 chars, 10 words
        timings = distribute_timing([long_word_piece, many_short_words], 0.0, 10.0)
        dur_a = timings[0][1] - timings[0][0]
        dur_b = timings[1][1] - timings[1][0]
        self.assertGreater(dur_a, dur_b)


class WrapLinesTests(unittest.TestCase):
    def test_short_text_one_line(self):
        self.assertEqual(wrap_lines("Hello"), ["Hello"])

    def test_long_text_wraps_to_two_lines(self):
        text = "This is a somewhat long line of subtitle text that needs wrapping"
        lines = wrap_lines(text)
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(len(l) <= 42 for l in lines))


class SegmentEndToEndTests(unittest.TestCase):
    def test_eda_regression_case_produces_readable_cues(self):
        # Real confirmed-good case from the Season 01 investigation.
        text = "Eda, Eda, my daughter, wake up!"
        cues = segment(text, 109.75, 113.30)
        self.assertEqual(len(cues), 1)
        self.assertIn("Eda", cues[0].text)
        self.assertIn("wake up", cues[0].text)

    def test_long_translation_produces_multiple_readable_cues_not_fragments(self):
        text = ("This is a very long English translation of the merged Turkish "
               "cues that will not possibly fit into two lines of subtitle "
               "text no matter how it is wrapped at all.")
        cues = segment(text, 66.0, 70.2)
        self.assertGreater(len(cues), 1)
        for c in cues:
            self.assertLessEqual(len(c.text), 84)
            self.assertGreaterEqual(len(c.text.split()), 2)   # no single-word fragments
        self.assertEqual(cues[0].start, 66.0)
        self.assertEqual(cues[-1].end, 70.2)

    def test_text_conservation(self):
        text = "First sentence here. Second sentence follows now. Third and final one."
        cues = segment(text, 0.0, 20.0)
        joined = " ".join(c.text for c in cues).split()
        self.assertEqual(joined, text.split())

    def test_multiple_sentences_split_at_sentence_boundaries_not_fragments(self):
        text = "I went home. Then I slept well."
        cues = segment(text, 0.0, 10.0)
        self.assertEqual(len(cues), 2)
        self.assertTrue(cues[0].text.endswith("."))
        self.assertTrue(cues[1].text.endswith("."))


if __name__ == "__main__":
    unittest.main()
