from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.pipeline.stage4_asr.engines.faster_whisper_engine import FasterWhisperEngine


def _fake_whisper_model():
    """A stand-in for faster_whisper.WhisperModel: transcribe() returns (segments, info)
    with no segments, which is all engine.transcribe() needs to build an empty ASRResult
    without touching real model weights."""
    fake_info = MagicMock(language="tr")
    model = MagicMock()
    model.transcribe.return_value = ([], fake_info)
    return model


@pytest.mark.unit
def test_hotwords_is_passed_through_to_model_transcribe():
    engine = FasterWhisperEngine(hotwords="Serkan Bolat, Eda Yıldız, Evren, Cenk")
    engine._model = _fake_whisper_model()

    engine.transcribe(Path("/fake.wav"), language="tr")

    _, kwargs = engine._model.transcribe.call_args
    assert kwargs["hotwords"] == "Serkan Bolat, Eda Yıldız, Evren, Cenk"


@pytest.mark.unit
def test_no_hotwords_passes_none_not_empty_string():
    engine = FasterWhisperEngine(hotwords=None)
    engine._model = _fake_whisper_model()

    engine.transcribe(Path("/fake.wav"), language="tr")

    _, kwargs = engine._model.transcribe.call_args
    assert kwargs["hotwords"] is None


@pytest.mark.unit
def test_empty_string_hotwords_also_passes_none():
    # An empty hotwords string is equivalent to "no hint" -- faster-whisper's own default
    # is None, not "", so this avoids sending a degenerate empty hint.
    engine = FasterWhisperEngine(hotwords="")
    engine._model = _fake_whisper_model()

    engine.transcribe(Path("/fake.wav"), language="tr")

    _, kwargs = engine._model.transcribe.call_args
    assert kwargs["hotwords"] is None
