"""scripts/hallucination_signal_distribution.py: the pure pieces (loop
shape, candidate graduated score, cache reading/filtering)."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "hallucination_signal_distribution",
    Path(__file__).resolve().parent.parent / "scripts" / "hallucination_signal_distribution.py")
hsd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hsd)


def _segment(start, end, text, cr, suppressed=False, score=0.0):
    return {"start": start, "end": end, "words": [{"text": text}], "compression_ratio": cr,
            "no_speech_prob": 0.3, "avg_logprob": -0.05, "hallucination_score": score,
            "suppressed": suppressed}


class LoopShapeTests(unittest.TestCase):
    def test_real_long_vowel_loop_is_loop_shaped(self):
        # The measured Hammer Session! S01E01 3124.7s segment.
        self.assertTrue(hsd.looks_like_repetition_loop("ー" * 80))

    def test_ordinary_sentence_is_not(self):
        self.assertFalse(hsd.looks_like_repetition_loop("危ないですよ、そんなもの"))

    def test_short_repeated_acknowledgement_is_not(self):
        # "Evet." said on its own: too short to be a loop, whatever it repeats.
        self.assertFalse(hsd.looks_like_repetition_loop("Evet."))


class GraduatedScoreTests(unittest.TestCase):
    def test_below_threshold_is_zero(self):
        self.assertEqual(hsd.graduated_compression_score(2.0, 10), 0.0)

    def test_at_threshold_matches_todays_step(self):
        self.assertEqual(hsd.graduated_compression_score(2.4, 10), 0.5)

    def test_extreme_ratio_caps_at_one(self):
        self.assertEqual(hsd.graduated_compression_score(51.46, 10), 1.0)


class CollectTests(unittest.TestCase):
    def test_filters_by_media_and_version(self):
        with tempfile.TemporaryDirectory() as d:
            for name, media, version in (("a", "/data/x/Show S01E01.mkv", "2.0.5"),
                                         ("b", "/data/y/Other S01E01.mkv", "2.0.5"),
                                         ("c", "/data/x/Show S01E02.mkv", "2.0.4")):
                Path(d, f"{name}.json").write_text(json.dumps({
                    "media_path": media, "pipeline_version": version,
                    "segments": [_segment(0, 25.4, "ー" * 80, 51.46, score=0.5)]}), encoding="utf-8")
            Path(d, "broken.json").write_text("{", encoding="utf-8")
            rows = hsd.collect(Path(d), ["Show"], "2.0.5")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["media"], "Show S01E01.mkv")
        self.assertTrue(rows[0]["loop_shaped"])
        self.assertEqual(rows[0]["compression_ratio"], 51.46)


class PercentileTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(hsd.percentiles([]), {})

    def test_max_is_p100(self):
        self.assertEqual(hsd.percentiles([1.0, 2.0, 51.46])["p100"], 51.46)


if __name__ == "__main__":
    unittest.main()
