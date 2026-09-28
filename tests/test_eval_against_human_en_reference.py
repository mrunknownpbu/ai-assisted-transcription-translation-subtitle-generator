"""scripts/eval_against_human_en_reference.py's pure scoring piece
(gap_coverage) -- the file-reading/chrF parts reuse eval_translation.py,
already covered by tests/test_eval_translation.py."""

import importlib.util
import unittest
from pathlib import Path

from srt import SrtCue

_spec = importlib.util.spec_from_file_location(
    "eval_against_human_en_reference",
    Path(__file__).resolve().parent.parent / "scripts" / "eval_against_human_en_reference.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


class GapCoverageTests(unittest.TestCase):
    def test_fully_covered_reference_cue_is_not_flagged(self):
        source = [SrtCue(0.0, 10.0, "source text")]
        reference = [SrtCue(2.0, 5.0, "human line")]
        self.assertEqual(ev.gap_coverage(source, reference), [])

    def test_reference_cue_with_no_source_at_all_is_flagged(self):
        # The real production shape: a 90-second window a human heard
        # dialogue in, but the source transcript has nothing overlapping
        # it at all (see the VAD-merged-gap bug, CLAUDE.md 2026-09-28/29).
        source = [SrtCue(0.0, 10.0, "before"), SrtCue(100.0, 105.0, "after")]
        reference = [SrtCue(40.0, 45.0, "missed dialogue")]
        flagged = ev.gap_coverage(source, reference)
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["start"], 40.0)
        self.assertEqual(flagged[0]["text"], "missed dialogue")

    def test_tiny_edge_overlap_still_counts_as_a_gap(self):
        # A source cue that only brushes the very edge of a much longer
        # reference cue must not count as "covered" -- min_overlap_frac
        # guards against that.
        source = [SrtCue(9.9, 10.1, "sliver")]
        reference = [SrtCue(0.0, 20.0, "long human line")]
        flagged = ev.gap_coverage(source, reference)
        self.assertEqual(len(flagged), 1)

    def test_substantial_overlap_is_not_flagged(self):
        source = [SrtCue(0.0, 15.0, "mostly covers it")]
        reference = [SrtCue(0.0, 20.0, "long human line")]
        self.assertEqual(ev.gap_coverage(source, reference), [])

    def test_sdh_markup_stripped_from_flagged_text(self):
        source = []
        reference = [SrtCue(0.0, 5.0, "(sighs) Actual dialogue")]
        flagged = ev.gap_coverage(source, reference)
        self.assertEqual(flagged[0]["text"], "Actual dialogue")


class FragmentationTests(unittest.TestCase):
    def test_real_three_way_split_counts_three_cues_per_human_cue(self):
        # The measured S01E01 case: one human cue, three of ours.
        ours = [SrtCue(1881.2, 1885.9, "Hey, can"), SrtCue(1885.9, 1892.7, "you"),
                SrtCue(1892.7, 1895.6, "read it?")]
        human = [SrtCue(1881.0, 1896.0, "What!? You want to fight me?")]
        f = ev.fragmentation(ours, human)
        self.assertEqual(f["cues_per_human_cue"], 3.0)
        self.assertEqual(f["split_human_cue_rate"], 1.0)
        self.assertEqual(f["short_cue_rate"], 1.0)  # all three are <= 10 chars
        self.assertEqual(f["mid_sentence_end_rate"], 1.0)

    def test_same_granularity_as_human(self):
        ours = [SrtCue(0.0, 2.0, "Hello there."), SrtCue(3.0, 5.0, "How are you doing?")]
        human = [SrtCue(0.0, 2.0, "Hi there."), SrtCue(3.0, 5.0, "How are you?")]
        f = ev.fragmentation(ours, human)
        self.assertEqual(f["cues_per_human_cue"], 1.0)
        self.assertEqual(f["split_human_cue_rate"], 0.0)
        self.assertEqual(f["mid_sentence_end_rate"], 0.0)
        self.assertEqual(f["ours"]["mean_duration"], 2.0)

    def test_songs_and_sound_only_human_cues_are_skipped(self):
        ours = [SrtCue(0.0, 1.0, "a"), SrtCue(1.0, 2.0, "b")]
        human = [SrtCue(0.0, 2.0, "♪ la la ♪"), SrtCue(0.0, 2.0, "(door slams)")]
        self.assertIsNone(ev.fragmentation(ours, human)["cues_per_human_cue"])

    def test_japanese_sentence_end_punctuation_counts(self):
        ours = [SrtCue(0.0, 1.0, "危ない。"), SrtCue(1.0, 2.0, "くそ！")]
        self.assertEqual(ev.fragmentation(ours, [])["mid_sentence_end_rate"], 0.0)


class ParseEpisodesTests(unittest.TestCase):
    def test_range(self):
        self.assertEqual(ev.parse_episodes("1-3"), [1, 2, 3])

    def test_list(self):
        self.assertEqual(ev.parse_episodes("1,3,5"), [1, 3, 5])

    def test_single(self):
        self.assertEqual(ev.parse_episodes("7"), [7])


if __name__ == "__main__":
    unittest.main()
