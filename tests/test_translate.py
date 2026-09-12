import unittest

from transcript import BoundaryReason, Segment, Word
from translate import build_context_spans


def cue(index, start, end, text, boundary=None):
    words = [Word(text=w, original_text=w, start=start, end=end) for w in text.split()]
    return Segment(index=index, start=start, end=end, words=words, avg_logprob=0.0,
                  no_speech_prob=0.0, compression_ratio=0.0, boundary_before=boundary)


class BuildContextSpansTests(unittest.TestCase):
    def test_real_acoustic_gap_forces_new_span_even_under_max_gap(self):
        cues = [cue(0, 107.843, 108.843, "Tamam"),
               cue(1, 109.748, 110.5, "Eda Eda", boundary=BoundaryReason.REAL_ACOUSTIC_GAP)]
        spans = build_context_spans(cues)
        self.assertEqual(spans, [[0], [1]])

    def test_display_boundary_does_not_force_a_new_span(self):
        cues = [cue(0, 0.0, 1.0, "a"), cue(1, 1.2, 2.0, "b", boundary=BoundaryReason.DISPLAY_SPLIT)]
        spans = build_context_spans(cues)
        self.assertEqual(spans, [[0, 1]])

    def test_sentence_end_closes_a_span(self):
        cues = [cue(0, 0.0, 1.0, "Hello there."), cue(1, 1.1, 2.0, "How are you?")]
        spans = build_context_spans(cues)
        self.assertEqual(spans, [[0], [1]])

    def test_large_gap_without_provenance_still_forces_a_break(self):
        cues = [cue(0, 0.0, 1.0, "Tamam kalktim"), cue(1, 10.0, 11.0, "Gunaydin dunya")]
        spans = build_context_spans(cues)
        self.assertEqual(spans, [[0], [1]])

    def test_ordinary_dialogue_pause_does_not_break_a_span(self):
        cues = [cue(0, 0.0, 1.0, "Eda"), cue(1, 1.8, 2.5, "neredesin")]
        spans = build_context_spans(cues)
        self.assertEqual(spans, [[0, 1]])


if __name__ == "__main__":
    unittest.main()
