"""Deterministic, inference-free tests for context-candidate evaluation."""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "eval_translation_context_candidates",
    Path(__file__).resolve().parent.parent / "scripts" / "eval_translation_context_candidates.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


def write_srt(path: Path, cues: list[tuple[float, float, str]]) -> None:
    def timestamp(seconds: float) -> str:
        millis = round(seconds * 1000)
        hours, millis = divmod(millis, 3_600_000)
        minutes, millis = divmod(millis, 60_000)
        seconds, millis = divmod(millis, 1000)
        return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"

    path.write_text("\n\n".join(
        f"{index}\n{timestamp(start)} --> {timestamp(end)}\n{text}"
        for index, (start, end, text) in enumerate(cues, 1)) + "\n", encoding="utf-8")


class ContextCandidateEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.directory = Path(self.temp.name)
        self.reference = self.directory / "episode.en.hi.srt"
        self.baseline = self.directory / "current.en.srt"
        self.candidate = self.directory / "neighbor.en.srt"
        write_srt(self.reference, [(0, 2, "(Eda) Hello."), (3, 5, "How are you?")])
        write_srt(self.baseline, [(0, 2, "Hello."), (3, 5, "How are you?")])
        write_srt(self.candidate, [(0, 2, "Hi."), (3, 5, "How are you?")])

    def tearDown(self):
        self.temp.cleanup()

    def test_score_uses_existing_human_pairing_and_chrf(self):
        score = ev.score_srt(self.baseline, ev._read_srt(self.reference))
        self.assertEqual(score["cues"], 2)
        self.assertEqual(score["pairs"], 2)
        self.assertEqual(score["paired_cues"], 2)
        self.assertEqual(score["pairing_rate"], 1.0)
        self.assertEqual(score["chrf"], 100.0)

    def test_comparison_only_counts_matching_timestamps(self):
        shifted = self.directory / "shifted.en.srt"
        write_srt(shifted, [(0.1, 2, "Hello."), (3, 5, "Changed.")])
        self.assertEqual(ev.compare_cues(self.baseline, shifted), {
            "same_index_timing_cues": 1,
            "text_differences_on_same_timing": 1,
        })

    def test_cli_writes_repeatable_measurement_report(self):
        report_path = self.directory / "report.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(ev.main([
                "--reference", str(self.reference), "--baseline", str(self.baseline),
                "--candidate", f"left-1-right-1={self.candidate}", "--json", str(report_path),
            ]), 0)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        result = report["candidates"][0]
        self.assertEqual(result["name"], "left-1-right-1")
        self.assertLess(result["chrf"], report["baseline"]["chrf"])
        self.assertEqual(result["delta_vs_baseline"]["pairs"], 0)
        self.assertEqual(result["comparison_to_baseline"]["text_differences_on_same_timing"], 1)
        self.assertIn("Baseline: chrF=100.0", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
