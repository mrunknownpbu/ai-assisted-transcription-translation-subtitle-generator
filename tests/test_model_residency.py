"""B2 (2026-09-28): NLLB stays loaded between jobs on a dedicated GPU
instead of a ~6-10s load per job, and is evicted on an idle timer or when
any other GPU model (ASR, the Analyze sampler) is about to load."""

import time
import unittest
from unittest.mock import patch

import gpu
import translate
from transcript import Segment, Word
from translate import TranslationConfig, _ResidentNllb, translate_spans


def cue(index, text):
    words = [Word(text=w, original_text=w, start=0.0, end=1.0) for w in text.split()]
    return Segment(index=index, start=0.0, end=1.0, words=words, avg_logprob=0.0,
                   no_speech_prob=0.0, compression_ratio=0.0, boundary_before=None)


class ResidencyTestCase(unittest.TestCase):
    def setUp(self):
        self.resident = _ResidentNllb()
        patches = [patch.object(translate, "_resident", self.resident),
                   patch.dict(gpu._residents, clear=True),
                   patch("translate.translate_batch", side_effect=lambda m, t, b, s, *a, **k: [x.upper() for x in s]),
                   patch("gpu.free_gpu")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.load = patch("translate.load_model", side_effect=lambda c, lang: (object(), f"tok-{lang}", 7)).start()
        self.addCleanup(patch.stopall)

    def translate_one(self, lang="tr"):
        return translate_spans([cue(0, "Merhaba")], [[0]], lang, config=TranslationConfig(device="cuda"))


class DisabledByDefaultTests(ResidencyTestCase):
    def test_bare_import_keeps_load_per_call(self):
        self.translate_one()
        self.translate_one()
        self.assertEqual(self.load.call_count, 2)
        self.assertFalse(self.resident.loaded)


class EnabledTests(ResidencyTestCase):
    def setUp(self):
        super().setUp()
        translate.enable_model_residency(60)
        self.addCleanup(self.resident.evict)

    def test_consecutive_jobs_load_once(self):
        self.assertEqual(self.translate_one(), ["MERHABA"])
        self.assertEqual(self.translate_one(), ["MERHABA"])
        self.assertEqual(self.load.call_count, 1)
        self.assertTrue(self.resident.loaded)
        self.assertTrue(translate.resident_model_loaded())

    def test_second_language_loads_only_a_tokenizer(self):
        self.translate_one("tr")
        with patch("translate.load_tokenizer", return_value="tok-ja") as tok:
            self.translate_one("ja")
        self.assertEqual(self.load.call_count, 1)
        tok.assert_called_once()

    def test_cpu_translation_never_uses_residency(self):
        translate_spans([cue(0, "Merhaba")], [[0]], "tr", config=TranslationConfig(device="cpu"))
        self.assertFalse(self.resident.loaded)

    def test_evict_frees_and_next_job_reloads(self):
        self.translate_one()
        self.assertTrue(self.resident.evict())
        self.assertFalse(self.resident.loaded)
        self.translate_one()
        self.assertEqual(self.load.call_count, 2)

    def test_evict_refused_while_translating(self):
        self.resident.acquire(TranslationConfig(device="cuda"), "tur_Latn")
        self.assertFalse(self.resident.evict())
        self.resident.release()
        self.assertTrue(self.resident.evict())

    def test_other_model_preflight_evicts_nllb(self):
        self.translate_one()
        gpu.preflight_vram_check(0.0, max_wait_seconds=0)  # what ASR / the sampler call
        self.assertFalse(self.resident.loaded)

    def test_nllb_own_preflight_keeps_it(self):
        self.translate_one()
        gpu.preflight_vram_check(0.0, max_wait_seconds=0, keep_resident="nllb")
        self.assertTrue(self.resident.loaded)

    def test_failed_load_leaves_nothing_resident(self):
        self.load.side_effect = RuntimeError("CUDA out of memory")
        with self.assertRaises(RuntimeError):
            self.translate_one()
        self.assertFalse(self.resident.loaded)
        self.load.side_effect = lambda c, lang: (object(), "tok", 7)
        self.translate_one()
        self.assertTrue(self.resident.loaded)


class IdleTimerTests(ResidencyTestCase):
    def test_idle_timer_evicts(self):
        translate.enable_model_residency(0.05)
        self.translate_one()
        deadline = time.monotonic() + 2
        while self.resident.loaded and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(self.resident.loaded)

    def test_zero_disables(self):
        translate.enable_model_residency(0)
        self.translate_one()
        self.translate_one()
        self.assertEqual(self.load.call_count, 2)


class IdleSecondsEnvTests(unittest.TestCase):
    def _value(self, **env):
        with patch.dict("os.environ", {"SUBTITLE_AI_GPU_SHARED": "", "SUBTITLE_AI_MODEL_IDLE_SECONDS": "",
                                       **env}):
            return translate.model_idle_seconds_from_env()

    def test_dedicated_default(self):
        self.assertEqual(self._value(), 600.0)

    def test_shared_default_frees_after_every_job(self):
        self.assertEqual(self._value(SUBTITLE_AI_GPU_SHARED="1"), 0.0)

    def test_explicit_value_wins(self):
        self.assertEqual(self._value(SUBTITLE_AI_GPU_SHARED="1", SUBTITLE_AI_MODEL_IDLE_SECONDS="30"), 30.0)

    def test_invalid_falls_back(self):
        self.assertEqual(self._value(SUBTITLE_AI_MODEL_IDLE_SECONDS="soon"), 600.0)
        self.assertEqual(self._value(SUBTITLE_AI_MODEL_IDLE_SECONDS="-1"), 600.0)


class EvictResidentsTests(unittest.TestCase):
    def test_a_failing_evictor_never_blocks_the_others(self):
        calls = []
        with patch.dict(gpu._residents, {"bad": lambda: 1 / 0, "good": lambda: calls.append(1)}, clear=True):
            gpu.evict_residents()
        self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()
