import unittest

from projection import ProjectedCue
from segmentation_target import MIN_DURATION, extend_short_cues


def pcue(start, end, text):
    return ProjectedCue(start, end, [text], 0, [])


class ExtendShortCuesTests(unittest.TestCase):
    def test_extends_a_too_short_cue_toward_min_duration(self):
        cues = [pcue(0.0, 0.18, "Eda,")]
        extend_short_cues(cues)
        self.assertGreaterEqual(cues[0].end - cues[0].start, MIN_DURATION - 1e-9)

    def test_never_overlaps_the_next_cue(self):
        cues = [pcue(0.0, 0.18, "Eda,"), pcue(0.5, 1.5, "next line here")]
        extend_short_cues(cues)
        self.assertLessEqual(cues[0].end, cues[1].start + 1e-9)

    def test_never_moves_start_earlier(self):
        cues = [pcue(5.0, 5.1, "Hi")]
        extend_short_cues(cues)
        self.assertEqual(cues[0].start, 5.0)

    def test_long_cue_is_not_shortened(self):
        cues = [pcue(0.0, 5.0, "A perfectly reasonably long cue already.")]
        extend_short_cues(cues)
        self.assertEqual(cues[0].end, 5.0)

    def test_never_exceeds_max_duration(self):
        cues = [pcue(0.0, 0.1, "x" * 300)]  # would want a huge duration by CPS
        extend_short_cues(cues, max_duration=7.0)
        self.assertLessEqual(cues[0].end - cues[0].start, 7.0 + 1e-9)

    def test_last_cue_has_no_next_cue_limit(self):
        cues = [pcue(100.0, 100.1, "short")]
        extend_short_cues(cues)
        self.assertGreaterEqual(cues[0].end - cues[0].start, MIN_DURATION - 1e-9)


class ProjectedCueLinesPreservedTests(unittest.TestCase):
    def test_text_property_joins_lines(self):
        cue = ProjectedCue(0.0, 1.0, ["First line", "second line"], 0, [])
        self.assertEqual(cue.text, "First line second line")
        self.assertEqual(cue.lines, ["First line", "second line"])


if __name__ == "__main__":
    unittest.main()
