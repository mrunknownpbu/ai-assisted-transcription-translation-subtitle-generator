import unittest
from unittest.mock import patch

from transcript import BoundaryReason, Segment, Word
from translate import TranslationConfig, build_context_spans, translate_spans


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


class TranslateSpansGpuLockTests(unittest.TestCase):
    """gpu_lock() serializes GPU contention (see gpu.py's module
    docstring) -- real defect (2026-09-17): it used to be acquired
    unconditionally whenever this call owns model construction, even for
    CPU-only translation, needlessly blocking the Analyze endpoint's
    stream-sampler (a real GPU consumer) for no protective reason."""

    def _run(self, device):
        cues = [cue(0, 0.0, 1.0, "Merhaba")]
        spans = [[0]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch", return_value=["Hello"]), \
             patch("gpu.gpu_lock") as mock_lock:
            translate_spans(cues, spans, "tr", config=TranslationConfig(device=device))
        return mock_lock

    def test_cpu_translation_does_not_take_the_gpu_lock(self):
        self._run("cpu").assert_not_called()

    def test_cuda_translation_still_takes_the_gpu_lock(self):
        self._run("cuda").assert_called_once()


if __name__ == "__main__":
    unittest.main()
