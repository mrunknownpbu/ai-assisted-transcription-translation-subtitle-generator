"""Multi-window majority-vote language detection -- the real defect this
guards against: a single early-window guess (faster-whisper's own
internal auto-detect) confidently picked the wrong language for an entire
episode with a multi-language cold open (a real "If You Love" S01E01:
Spanish at 60s, English at 180s, Turkish from ~10 minutes on). `model` is
faked throughout (no GPU dependency); `_extract_wav_window` (real ffmpeg)
is mocked out -- only `_majority_language`'s aggregation and
`detect_dominant_language`'s window-orchestration/no-speech-filtering
logic are under test here. The real fix's actual behavior against the
real episode was verified separately against real hardware.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
import wave
from dataclasses import dataclass
from unittest.mock import patch

import asr
from asr import _majority_language, detect_dominant_language


def _write_silent_wav(path: str, seconds: float, frame_rate: int = 16000) -> None:
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(frame_rate)
        f.writeframes(b"\x00\x00" * int(seconds * frame_rate))


@dataclass
class _FakeInfo:
    language: str
    language_probability: float


@dataclass
class _FakeSegment:
    text: str


class _ScriptedModel:
    """Returns one scripted (segments, info) pair per call, in order."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def transcribe(self, path, **kwargs):
        entry = self.script[self.calls]
        self.calls += 1
        if isinstance(entry, Exception):
            raise entry
        segments, info = entry
        return iter(segments), info


class MajorityLanguageTests(unittest.TestCase):
    def test_picks_the_most_common_language(self):
        votes = [("tr", 0.9), ("en", 0.6), ("tr", 0.95), ("tr", 0.8), ("es", 0.99)]
        result = _majority_language(votes)
        self.assertEqual(result[0], "tr")

    def test_returns_mean_confidence_of_the_winning_language(self):
        votes = [("tr", 1.0), ("tr", 0.5)]
        result = _majority_language(votes)
        self.assertEqual(result, ("tr", 0.75))

    def test_tie_broken_by_higher_mean_confidence(self):
        votes = [("tr", 0.5), ("en", 0.99)]
        result = _majority_language(votes)
        self.assertEqual(result[0], "en")

    def test_empty_votes_returns_none(self):
        self.assertIsNone(_majority_language([]))

    def test_agreement_weights_the_reported_confidence(self):
        # 3 of 5 windows agree on "tr", each individually confident.
        # Real defect: this used to report the winning windows' own mean
        # confidence (0.95) with zero regard for the 2 windows that
        # disagreed -- a contested vote and a unanimous one at the same
        # mean confidence reported identically.
        votes = [("tr", 0.95), ("tr", 0.96), ("tr", 0.94), ("en", 0.9), ("en", 0.92)]
        result = _majority_language(votes)
        self.assertEqual(result[0], "tr")
        mean_confidence = (0.95 + 0.96 + 0.94) / 3
        self.assertAlmostEqual(result[1], mean_confidence * (3 / 5))
        self.assertLess(result[1], mean_confidence)


class DetectDominantLanguageTests(unittest.TestCase):
    def test_majority_vote_overrides_an_unrepresentative_cold_open(self):
        # Mirrors the real case: the first two windows land on the cold
        # open (Spanish, then English), the rest land on the actual
        # dominant language (Turkish).
        script = [
            ([_FakeSegment("hola")], _FakeInfo("es", 0.95)),
            ([_FakeSegment("hello there")], _FakeInfo("en", 0.67)),
            ([_FakeSegment("merhaba")], _FakeInfo("tr", 0.99)),
            ([_FakeSegment("nasilsin")], _FakeInfo("tr", 1.0)),
            ([_FakeSegment("tamam")], _FakeInfo("tr", 1.0)),
        ]
        model = _ScriptedModel(script)
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            _write_silent_wav(f.name, seconds=1200)  # 20 minutes -- plenty for 5 windows
            with patch.object(asr, "_extract_wav_window"):
                result = detect_dominant_language(model, f.name)
        self.assertEqual(result[0], "tr")
        self.assertEqual(model.calls, 5)

    def test_silent_windows_are_excluded_from_the_vote(self):
        script = [
            ([], _FakeInfo("en", 0.3)),                          # no speech -- must not vote
            ([_FakeSegment("merhaba")], _FakeInfo("tr", 0.99)),
            ([], _FakeInfo("fr", 0.2)),                          # no speech -- must not vote
            ([_FakeSegment("nasilsin")], _FakeInfo("tr", 1.0)),
            ([_FakeSegment("")], _FakeInfo("es", 0.5)),           # blank text -- must not vote
        ]
        model = _ScriptedModel(script)
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            _write_silent_wav(f.name, seconds=1200)
            with patch.object(asr, "_extract_wav_window"):
                result = detect_dominant_language(model, f.name)
        self.assertEqual(result[0], "tr")

    def test_all_windows_silent_returns_none(self):
        script = [([], _FakeInfo("en", 0.1))] * 5
        model = _ScriptedModel(script)
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            _write_silent_wav(f.name, seconds=1200)
            with patch.object(asr, "_extract_wav_window"):
                result = detect_dominant_language(model, f.name)
        self.assertIsNone(result)

    def test_file_shorter_than_one_window_returns_none_without_calling_model(self):
        model = _ScriptedModel([])
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            _write_silent_wav(f.name, seconds=5)  # shorter than the 20s window
            result = detect_dominant_language(model, f.name)
        self.assertIsNone(result)
        self.assertEqual(model.calls, 0)

    def test_missing_file_returns_none_rather_than_raising(self):
        model = _ScriptedModel([])
        result = detect_dominant_language(model, "/no/such/file.wav")
        self.assertIsNone(result)

    def test_a_failed_window_extraction_does_not_abort_the_others(self):
        script = [
            ([_FakeSegment("merhaba")], _FakeInfo("tr", 0.99)),
            ([_FakeSegment("nasilsin")], _FakeInfo("tr", 1.0)),
        ]
        model = _ScriptedModel(script)
        call_count = {"n": 0}

        def flaky_extract(wav_path, offset, duration, out_path):
            call_count["n"] += 1
            if call_count["n"] <= 3:
                raise asr.MediaError("simulated ffmpeg failure")

        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            _write_silent_wav(f.name, seconds=1200)
            with patch.object(asr, "_extract_wav_window", side_effect=flaky_extract):
                result = detect_dominant_language(model, f.name)
        self.assertEqual(result[0], "tr")

    def test_a_failed_probe_transcription_does_not_abort_the_others(self):
        # Real defect: only MediaError from window EXTRACTION was ever
        # caught. A transcribe() failure on one probe window (this model
        # has a confirmed, recurring CUDA-OOM-under-contention failure
        # mode elsewhere in this codebase) used to propagate straight out
        # of detect_dominant_language() and fail the whole job, directly
        # contradicting this function's own "never raises" guarantee.
        script = [
            ([_FakeSegment("merhaba")], _FakeInfo("tr", 0.99)),
            RuntimeError("simulated CUDA OOM"),
            ([_FakeSegment("nasilsin")], _FakeInfo("tr", 1.0)),
            ([_FakeSegment("tamam")], _FakeInfo("tr", 1.0)),
            ([_FakeSegment("evet")], _FakeInfo("tr", 1.0)),
        ]
        model = _ScriptedModel(script)
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            _write_silent_wav(f.name, seconds=1200)
            with patch.object(asr, "_extract_wav_window"):
                result = detect_dominant_language(model, f.name)
        self.assertEqual(result[0], "tr")
        self.assertEqual(model.calls, 5)

    def test_duplicate_clamped_offsets_are_not_probed_twice(self):
        # duration=25s, window=20s (defaults): fractions (.1,.3,.5,.7,.9)
        # * 25 = (2.5,7.5,12.5,17.5,22.5), each clamped to min(25-20, x) =
        # min(5, x) -> (2.5, 5, 5, 5, 5) -- four of five fractions collapse
        # onto the SAME trailing-slice offset. Real defect: probing (and
        # voting on) that one slice four separate times let it dominate
        # the vote by repetition, not genuine agreement across the file.
        script = [
            ([_FakeSegment("merhaba")], _FakeInfo("tr", 0.9)),
            ([_FakeSegment("nasilsin")], _FakeInfo("tr", 1.0)),
        ]
        model = _ScriptedModel(script)
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            _write_silent_wav(f.name, seconds=25)
            with patch.object(asr, "_extract_wav_window"):
                result = detect_dominant_language(model, f.name)
        self.assertEqual(model.calls, 2)  # only the two distinct offsets, not five
        self.assertEqual(result[0], "tr")


if __name__ == "__main__":
    unittest.main()
