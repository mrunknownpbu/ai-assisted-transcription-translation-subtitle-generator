"""Real reference pairs from tests/fixtures/timing/ (none yet), plus tests of
the loader itself. See that directory's README for how to add the real pair."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import timing_reference as tr

_SPEC = importlib.util.spec_from_file_location(
    "eval_srt_quality", Path(__file__).parents[1] / "scripts" / "eval_srt_quality.py")
quality = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(quality)


class RealReferencePairTests(unittest.TestCase):
    def test_every_real_pair_holds(self):
        cases = tr.load_cases()
        if not cases:
            self.skipTest("no real timing reference pair has been added yet "
                          "(tests/fixtures/timing/README.md)")
        for case in cases:
            with self.subTest(case=case.name):
                timing = quality.evaluate(case.reference, case.candidate)["timing"]
                self.assertEqual(tr.check(case, timing), [], case.source)


class ManifestLoaderTests(unittest.TestCase):
    """These use throwaway files to test the loader; they are not timing evidence."""

    def _dir(self, manifest, files=("a.srt", "b.srt")):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        directory = Path(tmp.name)
        for name in files:
            (directory / name).write_text("", encoding="utf-8")
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return directory

    def _case(self, **overrides):
        case = {"name": "x", "reference": "a.srt", "candidate": "b.srt", "source": "test",
                "expected": {"classification": "constant_offset"}}
        case.update(overrides)
        return {"cases": [case]}

    def test_the_shipped_manifest_is_valid(self):
        tr.load_cases()

    def test_missing_manifest_or_no_cases_is_empty(self):
        self.assertEqual(tr.load_cases(Path(tempfile.gettempdir()) / "no-such-timing-dir"), [])
        self.assertEqual(tr.load_cases(self._dir({"cases": []})), [])

    def test_valid_case_loads(self):
        directory = self._dir(self._case(expected={"classification": "constant_offset",
                                                   "median_time_offset": 5.1, "tolerance": 0.3}))
        (case,) = tr.load_cases(directory)
        self.assertEqual((case.name, case.median_time_offset, case.tolerance), ("x", 5.1, 0.3))

    def test_rejects_malformed_entries(self):
        for manifest in (
                self._case(name=""), self._case(source=""),
                self._case(expected={"classification": "wobbly"}),
                self._case(expected={"classification": "constant_offset", "median_time_offset": 5.1}),
                self._case(reference="missing.srt")):
            with self.subTest(manifest=manifest), self.assertRaises(tr.ManifestError):
                tr.load_cases(self._dir(manifest))

    def test_check_reports_each_disagreement(self):
        directory = self._dir(self._case(expected={"classification": "constant_offset",
                                                   "median_time_offset": 5.1, "tolerance": 0.3}))
        (case,) = tr.load_cases(directory)
        ok = {"classification": "constant_offset", "median_time_offset": 5.0}
        self.assertEqual(tr.check(case, ok), [])
        bad = {"classification": "drift_or_nonlinear", "median_time_offset": 2.0}
        self.assertEqual(len(tr.check(case, bad)), 2)
        self.assertEqual(len(tr.check(case, {"classification": "constant_offset",
                                             "median_time_offset": None})), 1)


if __name__ == "__main__":
    unittest.main()
