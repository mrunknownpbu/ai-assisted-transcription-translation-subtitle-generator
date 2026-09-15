import numpy as np
import pytest

from app.pipeline.stage3_language_detection.detector import (
    _window_offsets, apply_manual_override, detect_language,
)
from tests.fixtures.synthetic_media import build_single_speech_fixture


class FakeLanguageIdEngine:
    """Deterministic stand-in for the real faster-whisper-backed engine, so this stage's
    orchestration logic (decoding + result shaping + override handling) is testable
    without downloading any model weights."""

    method_name = "fake_lid"

    def __init__(self, language="en", confidence=0.93, candidates=None):
        self.language = language
        self.confidence = confidence
        self.candidates = candidates or [("en", 0.93), ("nl", 0.04), ("de", 0.02)]

    def detect(self, pcm_16k_mono, sample_rate):
        assert pcm_16k_mono.size > 0
        return self.language, self.confidence, self.candidates


@pytest.mark.unit
def test_detect_language_uses_injected_engine(ffmpeg_fixture_dir):
    media_path = build_single_speech_fixture(ffmpeg_fixture_dir)
    engine = FakeLanguageIdEngine()

    result = detect_language(media_path, stream_index=0, engine=engine)

    assert result.detected_language == "en"
    assert result.effective_language == "en"
    assert result.confidence == pytest.approx(0.93)
    assert result.overridden is False
    assert result.method == "fake_lid"
    assert ("nl", 0.04) in result.candidates


@pytest.mark.unit
def test_manual_override_preserves_original_detection(ffmpeg_fixture_dir):
    media_path = build_single_speech_fixture(ffmpeg_fixture_dir)
    engine = FakeLanguageIdEngine(language="en", confidence=0.9)
    detected = detect_language(media_path, stream_index=0, engine=engine)

    overridden = apply_manual_override(detected, language="fr")

    assert overridden.overridden is True
    assert overridden.effective_language == "fr"
    # the original auto-detection is never discarded, only shadowed by the override
    assert overridden.detected_language == "en"
    assert overridden.confidence == pytest.approx(0.9)


class SequencedLanguageIdEngine:
    """Returns a different canned (language, confidence, candidates) tuple per call, in
    order — simulates each sampled window seeing genuinely different audio."""

    method_name = "fake_sequenced_lid"

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def detect(self, pcm_16k_mono, sample_rate):
        result = self._results[min(self.calls, len(self._results) - 1)]
        self.calls += 1
        return result


@pytest.mark.unit
def test_window_offsets_single_window_when_short():
    assert _window_offsets(duration_s=20.0, num_windows=4, window_seconds=30) == [0.0]


@pytest.mark.unit
def test_window_offsets_spread_across_middle_80_percent():
    offsets = _window_offsets(duration_s=1000.0, num_windows=4, window_seconds=30)

    assert len(offsets) == 4
    assert offsets[0] == pytest.approx(100.0)  # 10% mark
    assert offsets[-1] <= 900.0 - 30  # never starts a window inside the last 10%
    assert offsets == sorted(offsets)


@pytest.mark.unit
def test_multi_window_result_overrides_a_single_unrepresentative_window(ffmpeg_fixture_dir, monkeypatch):
    """Reproduces the real production failure mode directly: a cold-open-style first
    window disagrees with the rest of the episode. The aggregate must follow the
    majority, not the first window alone."""
    media_path = build_single_speech_fixture(ffmpeg_fixture_dir)
    monkeypatch.setattr(
        "app.pipeline.stage3_language_detection.detector.decode_pcm_array",
        lambda *a, **kw: np.ones(1600, dtype=np.float32),
    )
    # First (cold-open-like) window misdetects as English at low confidence; the next
    # three, sampled from the body of the episode, agree on Turkish with higher confidence.
    engine = SequencedLanguageIdEngine([
        ("en", 0.35, [("en", 0.35), ("tr", 0.30)]),
        ("tr", 0.81, [("tr", 0.81), ("en", 0.10)]),
        ("tr", 0.77, [("tr", 0.77), ("en", 0.08)]),
        ("tr", 0.79, [("tr", 0.79), ("en", 0.09)]),
    ])

    result = detect_language(media_path, stream_index=0, engine=engine, duration_s=7863.0, num_windows=4)

    assert result.detected_language == "tr"
    assert result.confidence > 0.35  # the winning aggregate, not the lone first-window score
    assert "multi_window" in result.method
    assert engine.calls == 4


@pytest.mark.unit
def test_multi_window_skips_windows_that_fail_to_decode(ffmpeg_fixture_dir, monkeypatch):
    media_path = build_single_speech_fixture(ffmpeg_fixture_dir)
    call_count = {"n": 0}

    def flaky_decode(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            return np.zeros(0, dtype=np.float32)  # simulates a window past end-of-stream
        return np.ones(1600, dtype=np.float32)

    monkeypatch.setattr("app.pipeline.stage3_language_detection.detector.decode_pcm_array", flaky_decode)
    engine = SequencedLanguageIdEngine([("tr", 0.8, [])] * 4)

    result = detect_language(media_path, stream_index=0, engine=engine, duration_s=7863.0, num_windows=4)

    assert result.detected_language == "tr"
    assert engine.calls == 3  # one window's empty decode was skipped, not counted as a result
