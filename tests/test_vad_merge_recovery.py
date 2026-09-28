"""recover_vad_merged_segments (asr.py): a real production bug (Hammer
Session! S01E02, 2026-09-28) -- a 90-second window of song + interleaved
spoken dialogue produced ZERO segments because faster-whisper's VAD
merged the whole span into one continuous "speech island" with no
internal break, and the decoder gave up on it. Isolated re-transcription
of that exact window with VAD off recovered real content spread across
the whole span. This pass detects that shape (long duration, sparse
text) and re-decodes just that window with VAD off, keeping the
replacement only if it holds more text than the original."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from asr import AsrConfig, VAD_MERGE_MIN_DURATION, recover_vad_merged_segments


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
    def test_short_segment_is_never_touched(self):
        raw = [_raw_segment(0.0, 5.0, "kısa bir cümle burada")]
        model = _RetryModel([])
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(out, raw)
        self.assertEqual(model.calls, [])

    def test_long_dense_segment_is_never_touched(self):
        # 20s but plenty of text -- a real, correctly-decoded monologue,
        # not a VAD-merged blob. Must not be re-decoded.
        dense_text = " ".join(f"kelime{i}" for i in range(60))
        raw = [_raw_segment(0.0, 20.0, dense_text)]
        model = _RetryModel([])
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(out, raw)
        self.assertEqual(model.calls, [])

    def test_long_sparse_segment_triggers_recovery_and_offsets_are_absolute(self):
        # The real shape: a 95-second span holding almost nothing.
        raw = [_raw_segment(10.0, 105.0, "a")]
        model = _RetryModel([["ichi", "ni", "san"], ["shi", "go"]])
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(len(model.calls), 1)
        self.assertFalse(model.calls[0]["vad_filter"])
        self.assertIsNone(model.calls[0].get("vad_parameters"))
        # Replacement segments, timestamps shifted into the full-episode
        # timeline (clip-relative 0.0 -> absolute 10.0, the original
        # segment's own start).
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["start"], 10.0)
        self.assertTrue(all(w["start"] >= 10.0 for seg in out for w in seg["words"]))

    def test_replacement_discarded_when_it_recovers_less_text(self):
        raw = [_raw_segment(0.0, VAD_MERGE_MIN_DURATION + 5, "a")]
        model = _RetryModel([])  # retry finds nothing at all
        with patch("asr._extract_wav_window"):
            out = recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertEqual(out, raw)

    def test_vad_is_off_and_condition_on_previous_text_is_off_for_the_retry(self):
        raw = [_raw_segment(0.0, 15.0, "a")]
        model = _RetryModel([["word"]])
        with patch("asr._extract_wav_window"):
            recover_vad_merged_segments(raw, "job.wav", model, AsrConfig(), "ja")
        self.assertFalse(model.calls[0]["condition_on_previous_text"])
        self.assertFalse(model.calls[0]["vad_filter"])


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
