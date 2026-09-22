"""srt.py had no dedicated test file before this -- only exercised
indirectly through srt_translation.py/auto_glossary.py/qc/output_qc.py's
own tests. Added alongside parse_lines() (IMPROVEMENT_PLAN.md 4.2) to
cover both the pre-existing parse()/render() contract and the new one,
and specifically to prove parse_lines() -> render() round-trips a
multi-line cue's line breaks exactly, where plain parse() -> render()
would silently collapse them (the real defect parse_lines() exists to
avoid)."""

import tempfile
import unittest
from pathlib import Path

import srt
from srt import SrtCue, SrtCueLines


SAMPLE = (
    "1\n00:00:01,000 --> 00:00:02,500\nHello there.\n\n"
    "2\n00:00:03,000 --> 00:00:04,000\nFirst line\nSecond line\n"
)


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "sample.srt"
        self.path.write_text(SAMPLE, encoding="utf-8")

    def test_parses_start_and_end_timestamps(self):
        cues = srt.parse(self.path)
        self.assertAlmostEqual(cues[0].start, 1.0)
        self.assertAlmostEqual(cues[0].end, 2.5)

    def test_multi_line_cue_text_is_space_joined(self):
        # parse()'s long-standing contract, relied on by its existing
        # QC/reference-comparison callers -- must not change.
        cues = srt.parse(self.path)
        self.assertEqual(cues[1].text, "First line Second line")

    def test_returns_plain_srtcue_objects(self):
        cues = srt.parse(self.path)
        self.assertIsInstance(cues[0], SrtCue)


class ParseLinesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "sample.srt"
        self.path.write_text(SAMPLE, encoding="utf-8")

    def test_same_timestamps_as_parse(self):
        lines_cues = srt.parse_lines(self.path)
        plain_cues = srt.parse(self.path)
        self.assertEqual([(c.start, c.end) for c in lines_cues], [(c.start, c.end) for c in plain_cues])

    def test_preserves_multi_line_cues_as_a_list(self):
        cues = srt.parse_lines(self.path)
        self.assertEqual(cues[1].lines, ["First line", "Second line"])

    def test_single_line_cue_is_a_one_element_list(self):
        cues = srt.parse_lines(self.path)
        self.assertEqual(cues[0].lines, ["Hello there."])

    def test_returns_srtcuelines_objects(self):
        cues = srt.parse_lines(self.path)
        self.assertIsInstance(cues[0], SrtCueLines)


class RenderRoundTripTests(unittest.TestCase):
    """The real defect parse_lines() exists to prevent: editing one cue's
    text must never reformat another cue's untouched line breaks."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "sample.srt"
        self.path.write_text(SAMPLE, encoding="utf-8")

    def test_plain_parse_then_render_collapses_multi_line_cues(self):
        # Documents the lossy behavior parse_lines() exists to avoid --
        # not a desired outcome, a regression guard on the OLD path so a
        # future change doesn't silently "fix" this by breaking parse()'s
        # own documented contract instead of using parse_lines().
        cues = srt.parse(self.path)
        rendered = srt.render(cues)
        self.assertIn("First line Second line", rendered)
        self.assertNotIn("First line\nSecond line", rendered)

    def test_parse_lines_then_render_preserves_multi_line_cues(self):
        cues = srt.parse_lines(self.path)
        rendered = srt.render(cues)
        self.assertIn("First line\nSecond line", rendered)

    def test_editing_one_cue_never_touches_anothers_line_breaks(self):
        cues = srt.parse_lines(self.path)
        cues[0].lines = ["Edited hello."]
        rendered = srt.render(cues)
        self.assertIn("Edited hello.", rendered)
        self.assertIn("First line\nSecond line", rendered)  # cue 1 untouched

    def test_full_round_trip_reparses_identically(self):
        cues = srt.parse_lines(self.path)
        rendered = srt.render(cues)
        reparsed = srt.parse_lines(Path(self._write(rendered)))
        self.assertEqual([c.lines for c in reparsed], [c.lines for c in cues])

    def _write(self, content: str) -> Path:
        out = Path(self.tmp.name) / "roundtrip.srt"
        out.write_text(content, encoding="utf-8")
        return out


if __name__ == "__main__":
    unittest.main()
