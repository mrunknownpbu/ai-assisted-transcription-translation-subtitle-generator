import importlib.util
import tempfile
import unittest
from pathlib import Path

import srt
import srt_offset_fixtures as fixtures


SPEC = importlib.util.spec_from_file_location("eval_srt_quality", Path(__file__).parents[1] / "scripts" / "eval_srt_quality.py")
quality = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quality)


class TimingEvaluationTests(unittest.TestCase):
    def _files(self, reference, candidate):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        ref, cand = Path(tmp.name) / "ref.srt", Path(tmp.name) / "candidate.srt"
        ref.write_text(reference, encoding="utf-8"); cand.write_text(candidate, encoding="utf-8")
        return ref, cand

    def test_identifies_constant_offset_without_drift(self):
        ref, cand = self._files(
            "1\n00:00:01,000 --> 00:00:02,000\nOne\n\n2\n00:00:10,000 --> 00:00:11,000\nTwo\n",
            "1\n00:00:06,000 --> 00:00:07,000\nOne\n\n2\n00:00:15,000 --> 00:00:16,000\nTwo\n")
        result = quality.evaluate(ref, cand)["timing"]
        self.assertEqual(result["classification"], "constant_offset")
        self.assertAlmostEqual(result["median_time_offset"], 5.0)
        self.assertAlmostEqual(result["estimated_drift"], 0.0)

    def test_identifies_linear_drift(self):
        ref, cand = self._files(
            "1\n00:00:01,000 --> 00:00:02,000\nOne\n\n2\n00:00:10,000 --> 00:00:11,000\nTwo\n",
            "1\n00:00:01,100 --> 00:00:02,100\nOne\n\n2\n00:00:11,000 --> 00:00:12,000\nTwo\n")
        result = quality.evaluate(ref, cand)["timing"]
        self.assertEqual(result["classification"], "drift_or_nonlinear")
        self.assertGreater(result["estimated_drift"], 0.01)


class ReportedOffsetRegressionTests(unittest.TestCase):
    """The reported ~5.1 s human/AI offset (docs/handover.md) could not be
    reproduced without the real pair, so these use a synthetic stand-in with
    a known offset. They pin what the evaluator must say about it -- chiefly
    that a constant shift is still reported as one when the two files are
    segmented differently, which time-overlap pairing got wrong: a 5.1 s
    shift against ~3 s cues paired every cue with the wrong neighbour."""

    def setUp(self):
        self.reference = fixtures.reference_cues()

    def _timing(self, candidate):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        ref, cand = Path(tmp.name) / "ref.srt", Path(tmp.name) / "candidate.srt"
        ref.write_text(srt.render(self.reference), encoding="utf-8")
        cand.write_text(srt.render(candidate), encoding="utf-8")
        return quality.evaluate(ref, cand)["timing"]

    def test_constant_offset_with_identical_segmentation(self):
        t = self._timing(fixtures.shifted(self.reference, fixtures.OFFSET))
        self.assertEqual(t["classification"], "constant_offset")
        self.assertAlmostEqual(t["median_time_offset"], fixtures.OFFSET, places=2)
        self.assertAlmostEqual(t["estimated_drift"], 0.0, places=4)

    def test_constant_offset_survives_resegmentation_and_jitter(self):
        t = self._timing(fixtures.resegmented(self.reference, fixtures.OFFSET))
        self.assertEqual(t["pairing"], "words")
        self.assertEqual(t["classification"], "constant_offset")
        self.assertAlmostEqual(t["mean_time_offset"], fixtures.OFFSET, delta=0.1)
        self.assertAlmostEqual(t["median_time_offset"], fixtures.OFFSET, delta=0.1)

    def test_negative_offset_is_signed(self):
        t = self._timing(fixtures.resegmented(self.reference, -fixtures.OFFSET))
        self.assertEqual(t["classification"], "constant_offset")
        self.assertAlmostEqual(t["median_time_offset"], -fixtures.OFFSET, delta=0.1)

    def test_aligned_files_report_no_offset(self):
        t = self._timing(fixtures.resegmented(self.reference, 0.0))
        self.assertEqual(t["classification"], "constant_offset")
        self.assertAlmostEqual(t["median_time_offset"], 0.0, delta=0.1)

    def test_offset_that_starts_midway_is_not_called_constant(self):
        t = self._timing(fixtures.stepped(self.reference, fixtures.OFFSET))
        self.assertEqual(t["classification"], "drift_or_nonlinear")
        # The median still shows the shifted half; the mean shows the blend.
        self.assertAlmostEqual(t["median_time_offset"], fixtures.OFFSET, delta=0.1)
        self.assertLess(t["mean_time_offset"], fixtures.OFFSET - 1.0)

    def test_linear_drift_is_not_called_constant(self):
        t = self._timing(fixtures.drifting(self.reference, 0.002))
        self.assertEqual(t["classification"], "drift_or_nonlinear")
        self.assertAlmostEqual(t["estimated_drift"], 0.002, delta=0.0005)

    def test_falls_back_to_cue_pairing_when_no_words_anchor(self):
        # Cues too short to share a 3-word run: the earlier cue-level method.
        t = self._timing([srt.SrtCue(c.start + 5.0, c.end + 5.0, c.text.split()[0])
                          for c in self.reference])
        self.assertEqual(t["pairing"], "cues")
