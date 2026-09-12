"""asr.transcribe()'s own passthrough of stream-selection provenance into
CanonicalTranscript -- a fake WhisperModel-shaped object stands in for
faster-whisper so this runs without a GPU."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from asr import AsrConfig, transcribe


@dataclass
class _FakeInfo:
    language: str = "tr"
    language_probability: float = 0.95
    duration: float = 1.0


class _FakeModel:
    model_size_or_path = "fake-model"

    def transcribe(self, wav_path, **kwargs):
        return iter([]), _FakeInfo()


class AsrProvenanceTests(unittest.TestCase):
    def test_stream_provenance_is_recorded_on_the_transcript(self):
        transcript = transcribe(
            "audio.wav", "video.mkv", "hash", 1, config=AsrConfig(language=None), model=_FakeModel(),
            embedded_stream_language="tur", stream_selection_mode="AUTO",
            stream_selection_reason="detected tr at 95% confidence")
        self.assertEqual(transcript.embedded_stream_language, "tur")
        self.assertEqual(transcript.stream_selection_mode, "AUTO")
        self.assertEqual(transcript.stream_selection_reason, "detected tr at 95% confidence")

    def test_defaults_are_safe_when_provenance_is_omitted(self):
        transcript = transcribe("audio.wav", "video.mkv", "hash", 0, model=_FakeModel())
        self.assertIsNone(transcript.embedded_stream_language)
        self.assertEqual(transcript.stream_selection_mode, "AUTO")
        self.assertEqual(transcript.stream_selection_reason, "")


if __name__ == "__main__":
    unittest.main()
