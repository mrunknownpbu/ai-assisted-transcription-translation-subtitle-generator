from __future__ import annotations

import unittest

from transcript import NO_SPACE_LANGUAGES, Segment, Word, join_words


def _word(text: str, start: float, end: float) -> Word:
    return Word(text=text, original_text=text, start=start, end=end, probability=0.9)


class JoinWordsTests(unittest.TestCase):
    def test_space_delimited_language_joins_with_space(self):
        self.assertEqual(join_words(["Cenk", "gitti"], "tr"), "Cenk gitti")

    def test_unknown_language_defaults_to_space_delimited(self):
        self.assertEqual(join_words(["hello", "world"], ""), "hello world")

    def test_japanese_joins_with_no_separator(self):
        # Real defect (2026-09-13 Alice in Borderland validation): naive
        # " ".join() on word-level ASR tokens produced "つ か 喋 れ や でも"
        # instead of the correct, unspaced "つか喋れやでも".
        self.assertEqual(join_words(["つか", "喋れ", "やでも"], "ja"), "つか喋れやでも")

    def test_mandarin_and_thai_are_also_unspaced(self):
        self.assertIn("zh", NO_SPACE_LANGUAGES)
        self.assertIn("th", NO_SPACE_LANGUAGES)
        self.assertEqual(join_words(["你好", "世界"], "zh"), "你好世界")

    def test_korean_is_space_delimited(self):
        # Unlike Japanese/Mandarin, modern Korean orthography uses spaces
        # between words -- must not be swept into NO_SPACE_LANGUAGES.
        self.assertNotIn("ko", NO_SPACE_LANGUAGES)
        self.assertEqual(join_words(["안녕하세요", "세계"], "ko"), "안녕하세요 세계")


class SegmentTextTests(unittest.TestCase):
    def test_text_property_uses_segment_language(self):
        seg = Segment(index=0, start=0.0, end=1.0,
                      words=[_word("つか", 0.0, 0.3), _word("喋れ", 0.3, 0.6), _word("やでも", 0.6, 1.0)],
                      avg_logprob=0.0, no_speech_prob=0.0, compression_ratio=0.0, language="ja")
        self.assertEqual(seg.text, "つか喋れやでも")

    def test_text_property_default_language_is_space_delimited(self):
        seg = Segment(index=0, start=0.0, end=1.0,
                      words=[_word("Cenk", 0.0, 0.3), _word("gitti", 0.3, 0.6)],
                      avg_logprob=0.0, no_speech_prob=0.0, compression_ratio=0.0)
        self.assertEqual(seg.text, "Cenk gitti")


if __name__ == "__main__":
    unittest.main()
