from __future__ import annotations

import unittest

from segmentation_source import (MAX_CUE_CHARS, MAX_JAPANESE_CUE_CHARS, MAX_LINE_CHARS,
                                 _merge_short_cues, build_cues)
from transcript import BoundaryReason, Segment, Word


def _word(text: str, start: float, end: float) -> Word:
    return Word(text=text, original_text=text, start=start, end=end, probability=0.9)


def _sentence(words_text: list[str], start: float = 0.0, step: float = 0.3) -> list[Word]:
    return [_word(t, start + i * step, start + i * step + step * 0.8) for i, t in enumerate(words_text)]


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
        n = MAX_JAPANESE_CUE_CHARS  # each word is 1 char; exact language-specific cap
        # Interval kept well under MAX_DURATION (7.0s) for the full run, so
        # only the character-length path is under test here, not timing.
        words = [_word("あ", i * 0.05, i * 0.05 + 0.04) for i in range(n)]
        cues = build_cues(words, language="ja")
        self.assertEqual(len(cues), 1, "unspaced length is exactly at the cap; must not split")
        self.assertEqual(len(cues[0].text), n)

    def test_japanese_sentence_splits_to_display_length(self):
        words = [_word("あ", i * 0.05, i * 0.05 + 0.04) for i in range(MAX_JAPANESE_CUE_CHARS + 1)]
        cues = build_cues(words, language="ja")
        self.assertGreater(len(cues), 1)
        self.assertTrue(all(len(cue.text) <= MAX_JAPANESE_CUE_CHARS for cue in cues))

    def test_default_language_keeps_prior_space_delimited_behavior(self):
        words = [_word("a", 0.0, 0.1), _word("b", 0.1, 0.2)]
        cues = build_cues(words)
        self.assertEqual(cues[0].text, "a b")


class SentenceSplitTests(unittest.TestCase):
    """Natural-dialogue rework: sentences split within an acoustic group,
    no minimum-length gluing (the old 12-char gate is gone)."""

    def test_short_sentence_before_a_pause_is_its_own_cue(self):
        # "Tamam." (6 chars) used to be glued onto the next sentence by the
        # old `len(cur_text) >= 12` gate -- a real, complete short cue now.
        words = _sentence(["Tamam."], start=0.0) + _sentence(["Valizin", "hazır", "mı?"], start=1.0)
        cues = build_cues(words, language="tr")
        self.assertEqual([c.text for c in cues], ["Tamam.", "Valizin hazır mı?"])

    def test_two_sentences_with_no_pause_still_split_at_the_period(self):
        words = _sentence(["Tamam.", "Gidelim", "hadi."], start=0.0, step=0.2)
        cues = build_cues(words, language="tr")
        self.assertEqual([c.text for c in cues], ["Tamam.", "Gidelim hadi."])
        self.assertEqual(cues[1].boundary_before, BoundaryReason.SENTENCE_END)

    def test_short_mid_phrase_fragment_merges_when_there_is_no_pause(self):
        first = _word("Her", 0.0, 0.2)
        rest = [_word("şey", 0.21, 0.5), _word("olur.", 0.51, 0.9)]
        cues = _merge_short_cues([
            Segment(0, first.start, first.end, [first], 0, 0, 0, language="tr"),
            Segment(1, rest[0].start, rest[-1].end, rest, 0, 0, 0,
                    boundary_before=BoundaryReason.DISPLAY_SPLIT, language="tr"),
        ], "tr")
        self.assertEqual([c.text for c in cues], ["Her şey olur."])

    def test_japanese_sentence_marks_split_without_spaces_and_keep_closers(self):
        words = _sentence(["「こんにちは。」", "次です！", "本当？』"], start=0.0, step=0.2)
        cues = build_cues(words, language="ja")
        self.assertEqual([c.text for c in cues], ["「こんにちは。」", "次です！", "本当？』"])
        self.assertEqual(cues[1].boundary_before, BoundaryReason.SENTENCE_END)
        self.assertEqual(cues[2].boundary_before, BoundaryReason.SENTENCE_END)

    def test_title_abbreviation_period_does_not_split_the_sentence(self):
        words = _sentence(["Dr.", "Serkan", "geldi."], start=0.0, step=0.2)
        cues = build_cues(words, language="tr")
        self.assertEqual([c.text for c in cues], ["Dr. Serkan geldi."])

    def test_real_acoustic_gap_still_forces_a_break_mid_sentence(self):
        words = [_word("Bekle", 0.0, 0.3), _word("biraz", 2.0, 2.3)]   # 1.7s gap > MAX_GAP
        cues = build_cues(words, language="tr")
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[1].boundary_before, BoundaryReason.REAL_ACOUSTIC_GAP)

    def test_long_sentence_is_split_at_a_clause_boundary_not_an_arbitrary_cutoff(self):
        text = ("Sana bunu söylemek istemiyordum ama artık gerçeği bilmen gerekiyor bence "
               "çünkü bu böyle devam edemez.")
        words = _sentence(text.split(), start=0.0, step=0.25)
        cues = build_cues(words, language="tr")
        self.assertGreater(len(cues), 1)
        self.assertTrue(all(len(c.text) <= MAX_CUE_CHARS for c in cues))
        self.assertEqual(" ".join(c.text for c in cues).split(), text.split())
        self.assertEqual(cues[1].boundary_before, BoundaryReason.DISPLAY_SPLIT)

    def test_lines_are_wrapped_to_max_line_chars(self):
        text = "Serkan ile Eda bugün sahile gidip uzun uzun konuştular ve çok güldüler beraber."
        words = _sentence(text.split(), start=0.0, step=0.25)
        cues = build_cues(words, language="tr")
        for c in cues:
            self.assertLessEqual(len(c.lines), 2)
            self.assertTrue(all(len(l) <= MAX_LINE_CHARS for l in c.lines))
            self.assertEqual(" ".join(c.lines), c.text)

    def test_unspaced_language_never_attempts_line_wrap(self):
        words = [_word("あ", i * 0.05, i * 0.05 + 0.04) for i in range(MAX_JAPANESE_CUE_CHARS)]
        cues = build_cues(words, language="ja")
        self.assertEqual(cues[0].lines, [cues[0].text])


class TurnWordIdsTests(unittest.TestCase):
    """SUBTITLE_AI_TURN_DETECTION (off by default -- see turns.py): a
    turn-marked word forces an acoustic-group break recorded as
    UTTERANCE_END, which translate.build_context_spans() treats as a
    real translation-context break (SENTENCE_END alone does not)."""

    def test_turn_word_forces_an_utterance_end_boundary(self):
        words = _sentence(["Tamam."], start=0.0) + _sentence(["Valizin", "hazır", "mı?"], start=0.3, step=0.2)
        turn_ids = frozenset({id(words[1])})   # "Valizin" starts the new turn
        cues = build_cues(words, language="tr", turn_word_ids=turn_ids)
        self.assertEqual([c.text for c in cues], ["Tamam.", "Valizin hazır mı?"])
        self.assertEqual(cues[1].boundary_before, BoundaryReason.UTTERANCE_END)

    def test_no_turn_word_ids_is_unchanged_behaviour(self):
        words = _sentence(["Tamam."], start=0.0) + _sentence(["Valizin", "hazır", "mı?"], start=0.3, step=0.2)
        cues = build_cues(words, language="tr")
        self.assertEqual(cues[1].boundary_before, BoundaryReason.SENTENCE_END)

    def test_unrecognised_word_id_has_no_effect(self):
        words = _sentence(["Tamam.", "Devam", "ediyor."], start=0.0, step=0.2)
        cues = build_cues(words, language="tr", turn_word_ids=frozenset({999999}))
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[1].boundary_before, BoundaryReason.SENTENCE_END)


if __name__ == "__main__":
    unittest.main()
