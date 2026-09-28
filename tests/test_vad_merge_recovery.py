"""recover_vad_merged_segments (asr.py): a real production bug (Hammer
Session! S01E02, 2026-09-28) -- a 90-second window of song + interleaved
spoken dialogue produced ZERO decoded segments in full-episode
production. Root cause: faster-whisper's VAD treats the whole span as
one continuous "speech island", and word_timestamps=True's alignment
pass then collapses on that unbroken span -- keeps the first ~5s, drops
the rest, VAD on or off, full-episode or isolated (confirmed directly by
decoding the isolated window both ways). Only isolating the gap AND
decoding it with VAD off recovers the content. This pass finds any gap
this long between decoded segments and re-decodes it in isolation with
VAD off, splicing back whatever (if anything) comes out; a genuinely
silent gap still relies on the existing hallucination.py gate
downstream, same as any other segment."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from asr import AsrConfig, GAP_MIN_DURATION, recover_vad_merged_segments


def _raw_segment(start, end, text):
    words = [{"word": " " + w, "start": start, "end": start, "probability": 0.9}
             for w in text.split()]
    return {"start": start, "end": end, "avg_logprob": -0.3, "no_speech_prob": 0.2,
            "compression_ratio": 1.5, "words": words}


class _RetryModel:
    """Records the retry-transcribe call and returns canned segments,
    relative to the clip (i.e. starting near 0), the way faster-whisper
    would for an isolated sub-window."""

    def __init__(self, retried_words_per_segment):
        self.calls = []
        self._retried_words_per_segment = retried_words_per_segment

    def transcribe(self, wav_path, **kwargs):
        self.calls.append(kwargs)

        class _W:
            def __init__(self, word, start, end):
                self.word, self.start, self.end, self.probability = word, start, end, 0.9

        class _Seg:
            def __init__(self, start, end, words):
                self.start, self.end = start, end
                self.words = words
                self.avg_logprob, self.no_speech_prob, self.compression_ratio = -0.3, 0.2, 1.5

        segs = []
        t = 0.0
        for words in self._retried_words_per_segment:
            ws = [_W(" " + w, t + i, t + i + 0.5) for i, w in enumerate(words)]
            segs.append(_Seg(t, t + len(words) + 0.5, ws))
            t += len(words) + 1.0
        return iter(segs), object()


class RecoverVadMergedSegmentsTests(unittest.TestCase):
    def test_no_gap_means_no_retry_call_at_all(self):
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
        # The confirmed real production shape: nothing at all between
        # 10.2s and 100.1s, not one sparse segment.
        raw = [_raw_segment(0.0, 10.2, "a"), _raw_segment(100.1, 105.0, "b")]
        model = _RetryModel([["ichi", "ni", "san"], ["shi", "go"]])
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
        raw = [_raw_segment(0.0, 10.2, "a"), _raw_segment(100.1, 105.0, "b")]
        model = _RetryModel([])  # isolated re-decode finds nothing either
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(out, raw)

    def test_trailing_gap_after_the_last_segment_uses_total_duration(self):
        raw = [_raw_segment(0.0, 5.0, "a")]
        model = _RetryModel([["word"]])
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja",
                                              total_duration=30.0)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(out), 2)
        self.assertGreaterEqual(out[1]["start"], 5.0)

    def test_trailing_gap_skipped_when_total_duration_unknown(self):
        raw = [_raw_segment(0.0, 5.0, "a")]
        model = _RetryModel([["word"]])
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja",
                                              total_duration=None)
        self.assertEqual(out, raw)
        self.assertEqual(model.calls, [])


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
