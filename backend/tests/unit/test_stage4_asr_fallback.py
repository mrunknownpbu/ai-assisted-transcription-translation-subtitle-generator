from pathlib import Path

import pytest

from app.config import Settings
from app.pipeline.interfaces import ASRResult, ASRSegment, ASRWord
from app.pipeline.stage4_asr.engines.base import ASREngineError
from app.pipeline.stage4_asr.fallback import (
    ASRUnavailableError, build_default_engine_chain, transcribe_with_fallback,
)


def _fake_result(engine_name: str) -> ASRResult:
    return ASRResult(
        engine=engine_name, model_version=f"{engine_name}:test", language="en",
        segments=[ASRSegment(segment_id="seg_00000", start=0.0, end=1.0, text="hi",
                              words=[ASRWord(word="hi", start=0.0, end=1.0, confidence=0.9)],
                              avg_confidence=0.9)],
    )


class FakeEngine:
    def __init__(self, name, available=True, unavailable_reason=None, raises=False):
        self.name = name
        self._available = available
        self._unavailable_reason = unavailable_reason
        self._raises = raises
        self.transcribe_called = False

    def is_available(self):
        return self._available, self._unavailable_reason

    def transcribe(self, audio_path, language):
        self.transcribe_called = True
        if self._raises:
            raise ASREngineError(f"{self.name} exploded")
        return _fake_result(self.name)


@pytest.mark.unit
def test_first_available_engine_succeeds_and_stops_chain():
    e1 = FakeEngine("primary")
    e2 = FakeEngine("secondary")

    outcome = transcribe_with_fallback(Path("/fake.wav"), "en", [e1, e2])

    assert outcome.result.engine == "primary"
    assert e2.transcribe_called is False
    assert [a.outcome for a in outcome.attempts] == ["succeeded"]


@pytest.mark.unit
def test_unavailable_engine_is_skipped_with_reason_logged():
    e1 = FakeEngine("primary", available=False, unavailable_reason="package not installed")
    e2 = FakeEngine("secondary")

    outcome = transcribe_with_fallback(Path("/fake.wav"), "en", [e1, e2])

    assert outcome.result.engine == "secondary"
    assert outcome.attempts[0].outcome == "skipped_unavailable"
    assert outcome.attempts[0].detail == "package not installed"
    assert outcome.attempts[1].outcome == "succeeded"


@pytest.mark.unit
def test_failed_engine_falls_through_to_next():
    e1 = FakeEngine("primary", raises=True)
    e2 = FakeEngine("secondary")

    outcome = transcribe_with_fallback(Path("/fake.wav"), "en", [e1, e2])

    assert outcome.result.engine == "secondary"
    assert outcome.attempts[0].outcome == "failed"
    assert "exploded" in outcome.attempts[0].detail


@pytest.mark.unit
def test_all_engines_exhausted_raises_with_full_attempt_log():
    e1 = FakeEngine("primary", available=False, unavailable_reason="no binary")
    e2 = FakeEngine("secondary", raises=True)

    with pytest.raises(ASRUnavailableError) as excinfo:
        transcribe_with_fallback(Path("/fake.wav"), "en", [e1, e2])

    assert len(excinfo.value.attempts) == 2
    assert excinfo.value.attempts[0].outcome == "skipped_unavailable"
    assert excinfo.value.attempts[1].outcome == "failed"


@pytest.mark.unit
def test_empty_engine_chain_raises_value_error():
    with pytest.raises(ValueError):
        transcribe_with_fallback(Path("/fake.wav"), "en", [])


@pytest.mark.unit
def test_build_default_engine_chain_threads_hotwords_only_into_faster_whisper(tmp_path):
    settings = Settings(asr_engine_chain=["faster_whisper", "vosk"], models_dir=tmp_path)
    engines = build_default_engine_chain(settings, hotwords="Serkan Bolat, Evren")

    by_name = {e.name: e for e in engines}
    assert by_name["faster_whisper"].hotwords == "Serkan Bolat, Evren"
    # vosk has no concept of hotwords at all -- it must not be forced to accept one.
    assert not hasattr(by_name["vosk"], "hotwords")


@pytest.mark.unit
def test_build_default_engine_chain_defaults_hotwords_to_none(tmp_path):
    settings = Settings(asr_engine_chain=["faster_whisper"], models_dir=tmp_path)
    engines = build_default_engine_chain(settings)
    assert engines[0].hotwords is None
