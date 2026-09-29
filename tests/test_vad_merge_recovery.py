"""recover_vad_merged_segments (asr.py): faster-whisper's VAD, over a
whole episode, can treat a long stretch (e.g. a song bridging every
pause between spoken lines) as one continuous "speech island", and
word_timestamps=True (required throughout this pipeline) then collapses
its own alignment on that span. Two real production shapes, both
confirmed by decoding the isolated span with VAD kept ON (reproduces the
exact same collapse -- it's the alignment pass, not full-episode
context) versus VAD OFF (recovers normal segments with real text):

* a GAP: no segment at all (Hammer Session! S01E02, 2026-09-28, a
  90-second window of song + dialogue, ZERO segments in production).
* a SPARSE segment: one segment nominally covers the span but holds
  almost no text (Hammer Session! S01E01, 2026-09-29, a 38.9s segment
  holding ~14 characters where a human subtitle has a full line).

Both are re-decoded in isolation with VAD off; a gap's result is always
spliced in (even empty, for a genuinely silent gap -- still gated by the
existing hallucination.py downstream, same as any other segment), a
sparse segment's result replaces it only if it recovers more text."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from asr import (
    AsrConfig, GAP_MIN_DURATION, SHORT_SPARSE_MAX_DENSITY,
    SHORT_SPARSE_MIN_DURATION, SPARSE_MAX_DENSITY, recover_vad_merged_segments,
)


def _raw_segment(start, end, text):
    words = [{"word": " " + w, "start": start, "end": start, "probability": 0.9}
             for w in text.split()]
    return {"start": start, "end": end, "avg_logprob": -0.3, "no_speech_prob": 0.2,
            "compression_ratio": 1.5, "words": words}


class _RetryModel:
    """Records every retry-transcribe call; `call_responses` gives one
    "words per recovered segment" list per expected call, popped in
    order (last one reused if there are more calls than responses)."""

    def __init__(self, call_responses, compression_ratio=1.5):
        self.calls = []
        self._responses = list(call_responses)
        self._compression_ratio = compression_ratio

    def transcribe(self, wav_path, **kwargs):
        self.calls.append(kwargs)
        words_per_segment = self._responses.pop(0) if self._responses else []

        class _W:
            def __init__(self, word, start, end):
                self.word, self.start, self.end, self.probability = word, start, end, 0.9

        class _Seg:
            def __init__(self, start, end, words, compression_ratio):
                self.start, self.end = start, end
                self.words = words
                self.avg_logprob, self.no_speech_prob = -0.3, 0.2
                self.compression_ratio = compression_ratio

        segs = []
        t = 0.0
        for words in words_per_segment:
            ws = [_W(" " + w, t + i, t + i + 0.5) for i, w in enumerate(words)]
            segs.append(_Seg(t, t + len(words) + 0.5, ws, self._compression_ratio))
            t += len(words) + 1.0
        return iter(segs), object()


class GapRecoveryTests(unittest.TestCase):
    def test_no_gap_and_no_sparse_segment_means_no_retry_call_at_all(self):
        raw = [_raw_segment(0.0, 5.0, "a"), _raw_segment(6.0, 10.0, "b")]
        model = _RetryModel([])
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(out, raw)
        self.assertEqual(model.calls, [])

    def test_short_gap_below_threshold_is_left_alone(self):
        raw = [_raw_segment(0.0, 5.0, "a"), _raw_segment(5.0 + GAP_MIN_DURATION - 1, 20.0, "b")]
        model = _RetryModel([])
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(out, raw)
        self.assertEqual(model.calls, [])

    def test_real_bug_shape_zero_segments_across_a_long_gap_is_recovered(self):
        # The confirmed real production shape (S01E02): nothing at all
        # between 10.2s and 100.1s, not one sparse segment.
        raw = [_raw_segment(0.0, 10.2, "a b c d e f g h i j"), _raw_segment(100.1, 105.0, "b")]
        model = _RetryModel([[["ichi", "ni", "san"], ["shi", "go"]]])
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(len(model.calls), 1)
        # VAD is OFF for the isolated re-decode -- confirmed necessary:
        # VAD-on (even isolated) still forms one continuous span that
        # word_timestamps=True's alignment then collapses on, exactly
        # like the original full-episode decode.
        self.assertFalse(model.calls[0]["vad_filter"])
        self.assertFalse(model.calls[0]["condition_on_previous_text"])
        # 2 original + 2 recovered, sorted, timestamps shifted into the
        # full-episode timeline (clip-relative 0.0 -> absolute 10.2, the
        # gap's own start).
        self.assertEqual(len(out), 4)
        starts = [s["start"] for s in out]
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(any(10.2 <= s["start"] < 100.1 for s in out[1:3]))

    def test_genuinely_silent_gap_recovers_nothing_and_stays_a_gap(self):
        raw = [_raw_segment(0.0, 10.2, "a b c d e f g h i j"), _raw_segment(100.1, 105.0, "b")]
        model = _RetryModel([[]])  # isolated re-decode finds nothing either
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(out, raw)

    def test_trailing_gap_after_the_last_segment_uses_total_duration(self):
        raw = [_raw_segment(0.0, 5.0, "a")]
        model = _RetryModel([[["word"]]])
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja",
                                              total_duration=30.0)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(out), 2)
        self.assertGreaterEqual(out[1]["start"], 5.0)

    def test_trailing_gap_skipped_when_total_duration_unknown(self):
        raw = [_raw_segment(0.0, 5.0, "a")]
        model = _RetryModel([[["word"]]])
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja",
                                              total_duration=None)
        self.assertEqual(out, raw)
        self.assertEqual(model.calls, [])


class SparseSegmentRecoveryTests(unittest.TestCase):
    def test_short_segment_is_never_touched_regardless_of_density(self):
        raw = [_raw_segment(0.0, 5.0, "a")]  # below GAP_MIN_DURATION
        model = _RetryModel([])
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(out, raw)
        self.assertEqual(model.calls, [])

    def test_high_ratio_loop_is_replaced_by_a_clean_isolated_retry(self):
        loop_text = "ウニイクラチュウトロ" * 14
        original = _raw_segment(10.2, 48.2, loop_text)
        original["compression_ratio"] = 14.87
        raw = [_raw_segment(0.0, 10.2, "a b c d e f g h i j"), original]
        model = _RetryModel([[["recovered", "dialogue"], ["more", "speech"]]])
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(len(model.calls), 1)
        self.assertFalse(model.calls[0]["vad_filter"])
        self.assertNotIn(original, out)
        self.assertGreater(len(out), len(raw))
        self.assertTrue(all(s["compression_ratio"] < 2.4 for s in out[1:]))

    def test_high_ratio_loop_is_kept_when_retry_is_still_repetitive(self):
        original = _raw_segment(10.2, 48.2, "ウニイクラチュウトロ" * 14)
        original["compression_ratio"] = 14.87
        raw = [_raw_segment(0.0, 10.2, "a b c d e f g h i j"), original]
        model = _RetryModel([[["ウニイクラチュウトロ" * 14]]], compression_ratio=14.87)
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(out, raw)
        self.assertEqual(len(model.calls), 1)

    def test_short_sparse_alignment_collapse_is_retried(self):
        duration = SHORT_SPARSE_MIN_DURATION + 2
        text = "x" * int(duration * SHORT_SPARSE_MAX_DENSITY * 0.5)
        raw = [_raw_segment(0.0, duration, text)]
        model = _RetryModel([[["recovered", "dialogue"]]])
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(len(model.calls), 1)
        self.assertFalse(model.calls[0]["vad_filter"])
        self.assertNotEqual(out, raw)

    def test_short_sparse_recovery_does_not_retry_dense_segment(self):
        duration = SHORT_SPARSE_MIN_DURATION + 2
        text = "x" * int(duration * SHORT_SPARSE_MAX_DENSITY * 1.5)
        raw = [_raw_segment(0.0, duration, text)]
        model = _RetryModel([])
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(out, raw)
        self.assertEqual(model.calls, [])

    def test_long_dense_segment_is_never_touched(self):
        # Plenty of text for its length -- a real, correctly-decoded
        # monologue, not a collapsed span.
        dense_text = " ".join(f"kotoba{i}" for i in range(60))
        raw = [_raw_segment(0.0, 20.0, dense_text)]
        model = _RetryModel([])
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(out, raw)
        self.assertEqual(model.calls, [])

    def test_real_bug_shape_long_sparse_segment_is_replaced(self):
        # The confirmed real production shape (S01E01): one 38.9s
        # segment holding ~14 characters, where isolated VAD-off decoding
        # recovers 6 real segments across the same span.
        raw = [_raw_segment(11.6, 50.5, "a")]  # 1 char over 38.9s, well under density
        model = _RetryModel([[["atsui", "na"], ["dayo"]]])
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(len(model.calls), 1)
        self.assertFalse(model.calls[0]["vad_filter"])
        self.assertEqual(len(out), 2)  # the original sparse segment is gone, replaced
        self.assertTrue(all(11.6 <= s["start"] for s in out))

    def test_sparse_segment_kept_when_retry_recovers_no_more_text(self):
        raw = [_raw_segment(0.0, GAP_MIN_DURATION + 5, "a")]
        model = _RetryModel([[]])  # retry finds nothing better
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(out, raw)

    def test_just_under_the_density_threshold_is_sparse(self):
        duration = GAP_MIN_DURATION
        text = "x" * int(duration * SPARSE_MAX_DENSITY * 0.5)
        raw = [_raw_segment(0.0, duration, text)]
        model = _RetryModel([[["y"]]])
        with patch("asr._extract_wav_window"):
            recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(len(model.calls), 1)

    def test_well_above_the_density_threshold_is_not_sparse(self):
        duration = GAP_MIN_DURATION
        text = "x" * int(duration * SPARSE_MAX_DENSITY * 4)
        raw = [_raw_segment(0.0, duration, text)]
        model = _RetryModel([])
        with patch("asr._extract_wav_window"):
            recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(len(model.calls), 0)


class VadMergeRecoveryIsRecordedInModelInfoTests(unittest.TestCase):
    def test_on_by_default_when_vad_is_on(self):
        import asr
        params = asr._model_info(AsrConfig(), "v").parameters
        self.assertTrue(params["vad_merge_recovery"])

    def test_off_when_vad_itself_is_off(self):
        import asr
        params = asr._model_info(AsrConfig(vad_filter=False), "v").parameters
        self.assertFalse(params["vad_merge_recovery"])


if __name__ == "__main__":
    unittest.main()
