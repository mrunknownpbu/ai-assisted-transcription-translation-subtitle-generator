"""Word.joins_previous: faster-whisper's leading-space marker, preserved
through asr.segments_from_raw and honoured by transcript.render_words --
the fix for "New York 'a" instead of "New York'a" (asr.py's docstring)."""

from __future__ import annotations

import unittest

from asr import segments_from_raw
from transcript import Word, render_words


def _raw_word(text: str, start: float = 0.0, end: float = 0.1) -> dict:
    return {"word": text, "start": start, "end": end, "probability": 0.9}


class SegmentsFromRawAttachmentTests(unittest.TestCase):
    def test_leading_space_marks_a_new_word(self):
        raw = [{"start": 0.0, "end": 1.0, "words": [_raw_word(" New"), _raw_word(" York")]}]
        seg = segments_from_raw(raw, language="en")[0]
        self.assertFalse(seg.words[0].joins_previous)
        self.assertFalse(seg.words[1].joins_previous)

    def test_no_leading_space_marks_a_continuation(self):
        raw = [{"start": 0.0, "end": 1.0, "words": [_raw_word(" York"), _raw_word("'a")]}]
        seg = segments_from_raw(raw, language="tr")[0]
        self.assertFalse(seg.words[0].joins_previous)
        self.assertTrue(seg.words[1].joins_previous)

    def test_text_and_original_text_are_stripped_of_the_marker_space(self):
        raw = [{"start": 0.0, "end": 1.0, "words": [_raw_word(" York"), _raw_word("'a")]}]
        seg = segments_from_raw(raw, language="tr")[0]
        self.assertEqual([w.text for w in seg.words], ["York", "'a"])


def w(text, joins_previous=False):
    return Word(text=text, original_text=text, start=0.0, end=0.1, joins_previous=joins_previous)


class RenderWordsTests(unittest.TestCase):
    def test_joins_previous_true_glues_with_no_space(self):
        self.assertEqual(render_words([w("New"), w("York"), w("'a", joins_previous=True)], "tr"), "New York'a")

    def test_joins_previous_false_is_space_delimited(self):
        self.assertEqual(render_words([w("Cenk"), w("gitti")], "tr"), "Cenk gitti")

    def test_old_cached_transcript_falls_back_to_apostrophe_heuristic(self):
        # joins_previous defaults False for transcripts cached before the
        # field existed -- the regex fallback still fixes the one shape
        # that was visibly wrong ("New York 'a").
        self.assertEqual(render_words([w("New"), w("York"), w("'a")], "tr"), "New York'a")

    def test_known_limitation_a_quoted_word_can_false_positive_the_fallback(self):
        # The apostrophe-suffix fallback only runs for OLD cached transcripts
        # (joins_previous defaults False on load); it can't tell a suffix
        # ("'a") from a quoted word ("'Alo?'") by text alone, so it glues
        # both -- accepted, since a fresh transcript sets joins_previous
        # directly from faster-whisper and never hits this path.
        self.assertEqual(render_words([w("dedi"), w("'Alo?'")], "tr"), "dedi'Alo?'")

    def test_no_space_language_ignores_joins_previous(self):
        self.assertEqual(render_words([w("つか"), w("喋れ", joins_previous=True)], "ja"), "つか喋れ")

    def test_empty_word_text_is_skipped(self):
        self.assertEqual(render_words([w("Cenk"), w(""), w("gitti")], "tr"), "Cenk gitti")


if __name__ == "__main__":
    unittest.main()
