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


class ComputeTypeFromEnvTests(unittest.TestCase):
    """New-GPU-host migration (2026-09-27): compute_type was hard-coded
    "int8" (correct for this deployment's pre-Volta GPU, wrong for an
    Ampere+ card with efficient float16 throughput). compose.yml forwards
    SUBTITLE_AI_COMPUTE_TYPE as `${SUBTITLE_AI_COMPUTE_TYPE:-}`, so an
    unset .env entry reaches the container as "" -- must not crash or
    silently misbehave, same blank-safety contract as every other
    SUBTITLE_AI_* knob this project forwards this way."""

    def _parse(self, value):
        with patch.dict(os.environ, {}, clear=False):
            if value is None:
                os.environ.pop("SUBTITLE_AI_COMPUTE_TYPE", None)
            else:
                os.environ["SUBTITLE_AI_COMPUTE_TYPE"] = value
            return asr._compute_type_from_env()

    def test_blank_falls_back_to_default(self):
        self.assertEqual(self._parse(""), "int8")

    def test_whitespace_only_falls_back_to_default(self):
        self.assertEqual(self._parse("   "), "int8")

    def test_unset_falls_back_to_default(self):
        self.assertEqual(self._parse(None), "int8")

    def test_valid_value_is_used(self):
        self.assertEqual(self._parse("float16"), "float16")

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(self._parse(" float16 "), "float16")

    def test_not_validated_against_a_fixed_set(self):
        # Deliberately not restricted to a known-good list here -- an
        # invalid value surfaces as ctranslate2's own clear error at model
        # load time (see the function's docstring), so this just passes
        # whatever non-blank string it's given straight through.
        self.assertEqual(self._parse("nonsense"), "nonsense")

    def test_module_import_survives_a_blank_value(self):
        # The actual production failure mode: importing asr.py itself.
        import importlib
        with patch.dict(os.environ, {"SUBTITLE_AI_COMPUTE_TYPE": ""}):
            importlib.reload(asr)
        importlib.reload(asr)  # restore normal state for every other test

    def test_asr_config_default_is_sourced_from_the_env_helper(self):
        # Not a second hardcoded literal duplicating _compute_type_from_env's
        # default -- AsrConfig.compute_type must actually be wired to it.
        self.assertEqual(AsrConfig().compute_type, asr.DEFAULT_COMPUTE_TYPE)


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


class AsrStylePromptTests(unittest.TestCase):
    """Natural-dialogue plan step 5 (gated, off by default): SUBTITLE_AI_ASR_STYLE."""

    def _style(self, value):
        with patch.dict(os.environ, {}, clear=False):
            if value is None:
                os.environ.pop("SUBTITLE_AI_ASR_STYLE", None)
            else:
                os.environ["SUBTITLE_AI_ASR_STYLE"] = value
            return asr.asr_style_prompt()

    def test_off_by_default(self):
        self.assertIsNone(self._style(None))
        self.assertIsNone(self._style(""))

    def test_unrecognised_value_is_off_not_an_error(self):
        self.assertIsNone(self._style("bogus"))

    def test_natural_selects_the_preset_and_it_is_not_title_case(self):
        prompt = self._style("natural")
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt, asr.ASR_STYLE_PRESETS["natural"])
        # The measured failure mode this avoids repeating (asr.py's own
        # hotwords docstring): a decoding bias that induces Title Case.
        words = [w for w in prompt.replace(",", " ").replace(".", " ").split() if w.isalpha()]
        self.assertTrue(any(w[0].islower() for w in words))

    def test_initial_prompt_is_recorded_in_model_info_so_cache_key_changes(self):
        a = asr._model_info(AsrConfig(), "v").parameters
        b = asr._model_info(AsrConfig(initial_prompt="Aa, gerçekten mi?"), "v").parameters
        self.assertIsNone(a["initial_prompt"])
        self.assertNotEqual(a["initial_prompt"], b["initial_prompt"])


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

    def test_initial_prompt_reaches_faster_whisper(self):
        model = _RecordingModel()
        asr.transcribe("a.wav", "v.mkv", "h", 0, model=model,
                       config=AsrConfig(language="tr", initial_prompt="Aa, gerçekten mi?"))
        self.assertEqual(model.kwargs["initial_prompt"], "Aa, gerçekten mi?")

    def test_default_initial_prompt_is_none(self):
        model = _RecordingModel()
        asr.transcribe("a.wav", "v.mkv", "h", 0, model=model, config=AsrConfig(language="tr"))
        self.assertIsNone(model.kwargs["initial_prompt"])


if __name__ == "__main__":
    unittest.main()
