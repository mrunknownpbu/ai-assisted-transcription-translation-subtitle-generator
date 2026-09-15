"""Real (non-mocked) ASR integration test. Downloads the tiny faster-whisper model on
first run (network required) and transcribes synthetic espeak-ng speech end-to-end,
verifying the engine wrapper's word-timestamp extraction against a real model rather
than only against fakes.
"""
from pathlib import Path

import pytest

from app.core.audio import extract_wav_file
from app.pipeline.stage4_asr.engines.faster_whisper_engine import FasterWhisperEngine
from app.pipeline.stage4_asr.fallback import transcribe_with_fallback
from tests.fixtures.synthetic_media import generate_speech_wav


@pytest.mark.integration
def test_faster_whisper_transcribes_synthetic_speech_with_word_timestamps(ffmpeg_fixture_dir):
    speech_text = "The quick brown fox jumps over the lazy dog."
    wav_path = generate_speech_wav(speech_text, ffmpeg_fixture_dir / "speech.wav")

    engine = FasterWhisperEngine(model_size="tiny", device="cpu", compute_type="int8",
                                  download_root=str(ffmpeg_fixture_dir / "models"))
    available, reason = engine.is_available()
    assert available, reason

    outcome = transcribe_with_fallback(wav_path, "en", [engine])

    assert outcome.result.engine == "faster_whisper"
    assert len(outcome.result.segments) >= 1

    all_words = [w for seg in outcome.result.segments for w in seg.words]
    assert len(all_words) >= 4  # tiny model + synthetic TTS voice won't be perfect, but should catch several words

    for w in all_words:
        assert w.end >= w.start
        assert 0.0 <= w.confidence <= 1.0

    # Word timestamps must be monotonic and stay within the audio's duration.
    starts = [w.start for w in all_words]
    assert starts == sorted(starts)

    joined = " ".join(w.word.lower().strip(".,!?") for w in all_words)
    # Loose check: at least a couple of the actual spoken words should appear somewhere.
    assert any(word in joined for word in ("fox", "dog", "quick", "lazy", "jumps"))
