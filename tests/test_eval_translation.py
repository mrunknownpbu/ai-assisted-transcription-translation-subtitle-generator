"""scripts/eval_translation.py's scoring pieces: chrF, SDH reference
cleaning, and source/reference cue pairing. The model-running parts are
exercised against real episodes (benchmark-results/), not here."""

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

_spec = importlib.util.spec_from_file_location(
    "eval_translation", Path(__file__).resolve().parent.parent / "scripts" / "eval_translation.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


def cue(start, end, text):
    return SimpleNamespace(start=start, end=end, text=text)


class ChrfTests(unittest.TestCase):
    def test_identical_is_100_and_disjoint_is_0(self):
        self.assertAlmostEqual(ev.chrf([ev.chrf_stats("Where is Eda?", "Where is Eda?")]), 100.0)
        self.assertEqual(ev.chrf([ev.chrf_stats("xyz", "abc")]), 0.0)

    def test_closer_paraphrase_scores_higher(self):
        ref = "I'm really tired today."
        close = ev.chrf([ev.chrf_stats("I'm very tired today.", ref)])
        far = ev.chrf([ev.chrf_stats("The weather is nice.", ref)])
        self.assertGreater(close, far)

    def test_corpus_level_aggregates_stats_not_scores(self):
        stats = [ev.chrf_stats("a b c", "a b c"), ev.chrf_stats("long sentence here", "other words")]
        self.assertNotAlmostEqual(ev.chrf(stats), (100 + ev.chrf(stats[1:])) / 2)

    def test_empty_corpus(self):
        self.assertEqual(ev.chrf([]), 0.0)


class CleanReferenceTests(unittest.TestCase):
    def test_strips_speaker_labels_sounds_and_dashes(self):
        self.assertEqual(ev.clean_reference("(Eda) Where are you? [door slams]"), "Where are you?")
        self.assertEqual(ev.clean_reference("- Hi.\n- Hello."), "Hi. Hello.")
        self.assertEqual(ev.clean_reference("♪ la la ♪"), "la la")

    def test_pure_sound_description_becomes_empty(self):
        self.assertEqual(ev.clean_reference("(phone ringing)"), "")


class PairCuesTests(unittest.TestCase):
    def test_one_to_one_timing_pairs(self):
        source = [cue(0.0, 2.0, "Merhaba."), cue(3.0, 5.0, "Nasılsın?")]
        reference = [cue(0.1, 1.9, "Hello."), cue(3.1, 4.9, "(Eda) How are you?")]
        self.assertEqual(ev.pair_cues(source, reference), [(0, "Hello."), (1, "How are you?")])

    def test_two_reference_cues_inside_one_source_cue_are_joined(self):
        source = [cue(0.0, 4.0, "Merhaba. Nasılsın?")]
        reference = [cue(0.0, 1.8, "Hello."), cue(2.0, 4.0, "How are you?")]
        self.assertEqual(ev.pair_cues(source, reference), [(0, "Hello. How are you?")])

    def test_reference_straddling_two_source_cues_goes_to_the_larger_overlap_only(self):
        source = [cue(0.0, 2.0, "a"), cue(2.0, 4.0, "b")]
        reference = [cue(1.5, 4.0, "long line")]
        self.assertEqual(ev.pair_cues(source, reference), [(1, "long line")])

    def test_lyrics_and_sound_only_and_unmatched_are_skipped(self):
        source = [cue(0.0, 2.0, "şarkı"), cue(3.0, 5.0, "..."), cue(6.0, 8.0, "yalnız")]
        reference = [cue(0.0, 2.0, '"I am not mature enough"'), cue(3.0, 5.0, "(door opens)")]
        self.assertEqual(ev.pair_cues(source, reference), [])


class EpisodeSpecTests(unittest.TestCase):
    def test_ranges_and_lists(self):
        self.assertEqual(ev.parse_episodes("6-8"), [6, 7, 8])
        self.assertEqual(ev.parse_episodes("1,3,5-6"), [1, 3, 5, 6])


if __name__ == "__main__":
    unittest.main()
