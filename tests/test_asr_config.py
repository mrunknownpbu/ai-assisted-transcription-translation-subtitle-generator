"""ASR decoding defaults chosen from measured E01 data (asr.py module
docstring): hotwords opt-in, permissive VAD passed through to
faster-whisper, and both part of the transcript cache identity."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import asr
from asr import AsrConfig, hotwords_enabled, vad_parameters


class HotwordsFlagTests(unittest.TestCase):
    def _flag(self, value):
        with patch.dict(os.environ, {}, clear=False):
            if value is None:
                os.environ.pop("SUBTITLE_AI_ASR_HOTWORDS", None)
            else:
                os.environ["SUBTITLE_AI_ASR_HOTWORDS"] = value
            return hotwords_enabled()

    def test_off_when_unset_or_empty(self):
        self.assertFalse(self._flag(None))
        self.assertFalse(self._flag(""))

    def test_off_for_unrecognised_values(self):
        for v in ("off", "0", "false", "maybe"):
            self.assertFalse(self._flag(v), v)

    def test_on_for_truthy_values_case_insensitive(self):
        for v in ("on", "ON", "1", "true", " Yes "):
            self.assertTrue(self._flag(v), v)


class VadParametersTests(unittest.TestCase):
    def test_defaults_are_the_measured_permissive_settings(self):
        self.assertEqual(vad_parameters(AsrConfig()),
                         {"onset": 0.3, "offset": 0.15,
                          "min_silence_duration_ms": 1000, "speech_pad_ms": 500})

    def test_none_when_vad_disabled(self):
        self.assertIsNone(vad_parameters(AsrConfig(vad_filter=False)))

    def test_config_overrides_flow_through(self):
        cfg = AsrConfig(vad_onset=0.6, vad_offset=0.4, vad_min_silence_ms=2000, vad_speech_pad_ms=100)
        self.assertEqual(vad_parameters(cfg),
                         {"onset": 0.6, "offset": 0.4,
                          "min_silence_duration_ms": 2000, "speech_pad_ms": 100})

    def test_vad_is_recorded_in_model_info_so_cache_key_changes(self):
        a = asr._model_info(AsrConfig(), "v").parameters
        b = asr._model_info(AsrConfig(vad_onset=0.5), "v").parameters
        self.assertIn("vad_parameters", a)
        self.assertNotEqual(a["vad_parameters"], b["vad_parameters"])

    def test_default_hotwords_none(self):
        self.assertIsNone(AsrConfig().hotwords)


class _RecordingModel:
    model_size_or_path = "fake-model"

    def __init__(self):
        self.kwargs = None

    def transcribe(self, wav_path, **kwargs):
        self.kwargs = kwargs

        class _Info:
            language = "tr"
            language_probability = 0.95
            duration = 1.0
        return iter([]), _Info()


class TranscribePassesDecodingOptionsTests(unittest.TestCase):
    def test_vad_parameters_and_hotwords_reach_faster_whisper(self):
        model = _RecordingModel()
        asr.transcribe("a.wav", "v.mkv", "h", 0, model=model,
                       config=AsrConfig(language="tr", hotwords="Eda Serkan"))
        self.assertTrue(model.kwargs["vad_filter"])
        self.assertEqual(model.kwargs["vad_parameters"]["onset"], 0.3)
        self.assertEqual(model.kwargs["hotwords"], "Eda Serkan")

    def test_no_hotwords_and_no_vad_are_passed_as_none(self):
        model = _RecordingModel()
        asr.transcribe("a.wav", "v.mkv", "h", 0, model=model,
                       config=AsrConfig(language="tr", vad_filter=False))
        self.assertFalse(model.kwargs["vad_filter"])
        self.assertIsNone(model.kwargs["vad_parameters"])
        self.assertIsNone(model.kwargs["hotwords"])


if __name__ == "__main__":
    unittest.main()
