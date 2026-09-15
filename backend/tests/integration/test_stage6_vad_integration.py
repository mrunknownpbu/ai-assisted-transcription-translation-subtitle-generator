"""Real VAD integration test using the Silero VAD ONNX model bundled with faster-whisper."""
import pytest

from app.core.audio import decode_pcm_array
from app.pipeline.stage6_hallucination_defense.defense import compute_voice_activity_spans
from tests.fixtures.synthetic_media import build_multi_stream_fixture


@pytest.mark.integration
def test_vad_detects_speech_and_not_silence(ffmpeg_fixture_dir):
    media_path = build_multi_stream_fixture(ffmpeg_fixture_dir)  # [0] tone, [1] speech, [2] silence

    speech_pcm = decode_pcm_array(media_path, stream_index=1)
    silence_pcm = decode_pcm_array(media_path, stream_index=2)

    speech_spans = compute_voice_activity_spans(speech_pcm)
    silence_spans = compute_voice_activity_spans(silence_pcm)

    assert len(speech_spans) >= 1
    assert len(silence_spans) == 0
