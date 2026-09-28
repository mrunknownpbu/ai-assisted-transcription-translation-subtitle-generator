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


class ParseEpisodesTests(unittest.TestCase):
    def test_range(self):
        self.assertEqual(ev.parse_episodes("1-3"), [1, 2, 3])

    def test_list(self):
        self.assertEqual(ev.parse_episodes("1,3,5"), [1, 3, 5])

    def test_single(self):
        self.assertEqual(ev.parse_episodes("7"), [7])


if __name__ == "__main__":
    unittest.main()
