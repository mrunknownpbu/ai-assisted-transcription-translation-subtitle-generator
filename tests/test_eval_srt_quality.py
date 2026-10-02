import importlib.util
import tempfile
import unittest
from pathlib import Path


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
