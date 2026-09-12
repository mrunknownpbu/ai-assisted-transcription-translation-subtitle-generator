from __future__ import annotations

import unittest

from segmentation_source import MAX_CUE_CHARS, build_cues
from transcript import Word


def _word(text: str, start: float, end: float) -> Word:
    return Word(text=text, original_text=text, start=start, end=end, probability=0.9)


class BuildCuesLanguageTests(unittest.TestCase):
    def test_japanese_cue_text_has_no_inserted_spaces(self):
        words = [_word("つか", 0.0, 0.3), _word("喋れ", 0.3, 0.6), _word("やでも", 0.6, 1.0)]
        cues = build_cues(words, language="ja")
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, "つか喋れやでも")

    def test_english_cue_text_still_space_delimited(self):
        words = [_word("hello", 0.0, 0.3), _word("world", 0.3, 0.6)]
        cues = build_cues(words, language="en")
        self.assertEqual(cues[0].text, "hello world")

    def test_japanese_length_estimate_does_not_count_phantom_spaces(self):
        # Real defect (2026-09-13): the MAX_CUE_CHARS check joined words
        # with " " to estimate length regardless of language, so a run of
        # short Japanese words was penalized by one phantom character per
        # word-boundary that would never actually appear in the rendered
        # text -- splitting cues earlier than MAX_CUE_CHARS actually allows.
        # Build a word list whose real (unspaced) length is under the cap
        # but whose naively-spaced length would exceed it.
        n = MAX_CUE_CHARS  # each word is 1 char, so n words = n chars unspaced
        # Interval kept well under MAX_DURATION (7.0s) for the full run, so
        # only the character-length path is under test here, not timing.
        words = [_word("あ", i * 0.05, i * 0.05 + 0.04) for i in range(n)]
        cues = build_cues(words, language="ja")
        self.assertEqual(len(cues), 1, "unspaced length is exactly at the cap; must not split")
        self.assertEqual(len(cues[0].text), n)

    def test_default_language_keeps_prior_space_delimited_behavior(self):
        words = [_word("a", 0.0, 0.1), _word("b", 0.1, 0.2)]
        cues = build_cues(words)
        self.assertEqual(cues[0].text, "a b")


if __name__ == "__main__":
    unittest.main()
