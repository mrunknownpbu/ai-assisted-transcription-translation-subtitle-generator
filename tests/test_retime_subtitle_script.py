import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import srt
from test_retime import make_programme

_spec = importlib.util.spec_from_file_location(
    "retime_subtitle", Path(__file__).resolve().parent.parent / "scripts" / "retime_subtitle.py")
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


class RetimeScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.truth, words = make_programme(n=200)
        self.video = self.dir / "film.mkv"
        cache = self.dir / "cache"
        cache.mkdir()
        (cache / "t.json").write_text(json.dumps({
            "media_path": str(self.video), "language": "tr", "created_at": 1,
            "segments": [{"suppressed": False, "words": [{"text": w.text, "start": w.start, "end": w.end}
                                                         for w in words]}]}))
        self.cache = str(cache)

    @staticmethod
    def two_lines(text):
        words = text.split()
        half = len(words) // 2
        return [" ".join(words[:half]), " ".join(words[half:])]

    def write_subtitle(self, name, shift):
        path = self.dir / name
        path.write_text(srt.render([srt.SrtCueLines(c.start + shift, c.end + shift, self.two_lines(c.text))
                                    for c in self.truth]), encoding="utf-8")
        return path

    def run_script(self, *args):
        with mock.patch.object(sys, "argv", ["retime_subtitle.py", str(self.video), *map(str, args), "--language", "tr",
                                             "--cache-dir", self.cache]):
            return script.main()

    def test_writes_same_text_with_corrected_times(self):
        src = self.write_subtitle("late.srt", 5.1)
        out = self.dir / "fixed.srt"
        self.assertEqual(self.run_script(src, "--out", out), 0)
        fixed = srt.parse_lines(out)
        self.assertEqual([c.lines for c in fixed], [self.two_lines(c.text) for c in self.truth])
        self.assertLess(max(abs(f.start - t.start) for f, t in zip(fixed, self.truth)), 0.2)
        self.assertEqual(srt.parse_lines(src)[0].start, self.truth[0].start + 5.1)   # input untouched

    def test_in_place_replaces_the_input(self):
        src = self.write_subtitle("late.srt", -3.0)
        self.assertEqual(self.run_script(src, "--in-place"), 0)
        self.assertLess(abs(srt.parse_lines(src)[0].start - self.truth[0].start), 0.2)

    def test_dry_run_writes_nothing(self):
        src = self.write_subtitle("late.srt", 5.1)
        before = src.read_text()
        self.assertEqual(self.run_script(src, "--dry-run"), 0)
        self.assertEqual(src.read_text(), before)

    def test_aligned_subtitle_is_left_alone(self):
        src = self.write_subtitle("good.srt", 0.0)
        out = self.dir / "fixed.srt"
        self.assertEqual(self.run_script(src, "--out", out), 0)
        self.assertFalse(out.exists())

    def test_wrong_programme_is_refused_and_nothing_written(self):
        other, _ = make_programme(n=200, seed=77)
        src = self.dir / "other.srt"
        src.write_text(srt.render([srt.SrtCueLines(c.start, c.end, [c.text.replace("kelime", "baska")])
                                   for c in other]), encoding="utf-8")
        out = self.dir / "fixed.srt"
        self.assertEqual(self.run_script(src, "--out", out), 2)
        self.assertFalse(out.exists())

    def test_protected_subtitle_name_is_never_written(self):
        from errors import OutputSafetyError
        src = self.write_subtitle("film.en.hi.srt", 5.1)
        with self.assertRaises(OutputSafetyError):
            self.run_script(src, "--in-place")


if __name__ == "__main__":
    unittest.main()
