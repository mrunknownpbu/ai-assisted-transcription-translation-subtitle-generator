"""Turkish uses its own one-pair model (translate.DEDICATED_TRANSLATORS); every
other language keeps NLLB. Decision: docs/decisions/2026-10-04-turkish-dedicated-translator.md."""

import unittest
from unittest.mock import MagicMock, patch

import translate
from transcript import Segment, Word
from translate import NO_FORCED_BOS, TranslationConfig, engine_config, is_dedicated, translate_spans

MARIAN = "Helsinki-NLP/opus-mt-tc-big-tr-en"


def cue(text):
    words = [Word(text=w, original_text=w, start=0.0, end=1.0) for w in text.split()]
    return Segment(index=0, start=0.0, end=1.0, words=words, avg_logprob=0.0,
                   no_speech_prob=0.0, compression_ratio=0.0, boundary_before=None)


class EngineSelectionTests(unittest.TestCase):
    def test_turkish_gets_the_dedicated_model_on_the_hf_backend(self):
        config = engine_config(TranslationConfig(backend="ct2"), "tr")
        self.assertEqual(config.repo, MARIAN)
        self.assertEqual(config.backend, "hf")
        self.assertTrue(is_dedicated(config))

    def test_other_languages_keep_nllb(self):
        base = TranslationConfig()
        self.assertIs(engine_config(base, "ja"), base)
        self.assertFalse(is_dedicated(base))

    def test_batch_and_beam_settings_carry_over(self):
        config = engine_config(TranslationConfig(batch_size=5, num_beams=3), "tr")
        self.assertEqual((config.batch_size, config.num_beams), (5, 3))

    def test_can_be_turned_off(self):
        base = TranslationConfig(dedicated_translators=False)
        self.assertIs(engine_config(base, "tr"), base)

    def test_environment_switch(self):
        with patch.dict("os.environ", {"SUBTITLE_AI_DEDICATED_TRANSLATORS": "0"}):
            self.assertFalse(TranslationConfig().dedicated_translators)
        with patch.dict("os.environ", {"SUBTITLE_AI_DEDICATED_TRANSLATORS": ""}):
            self.assertTrue(TranslationConfig().dedicated_translators)


class GenerationTests(unittest.TestCase):
    def run_batch(self, bos):
        model, tok = MagicMock(), MagicMock()
        tok.return_value.to.return_value = {"input_ids": 1}
        tok.batch_decode.return_value = ["Hello"]
        with patch.dict("sys.modules", {"torch": MagicMock()}):
            translate._generate_one_batch(model, tok, bos, ["Merhaba"], "cpu", TranslationConfig())
        return model.generate.call_args.kwargs

    def test_marian_is_not_given_a_forced_start_token(self):
        self.assertIsNone(self.run_batch(NO_FORCED_BOS)["forced_bos_token_id"])

    def test_nllb_still_forces_english(self):
        self.assertEqual(self.run_batch(256047)["forced_bos_token_id"], 256047)


class PipelineTests(unittest.TestCase):
    def test_turkish_job_loads_the_dedicated_model_and_ignores_the_remote_server(self):
        loaded = []

        def fake_load(config, code):
            loaded.append(config.repo)
            return object(), object(), NO_FORCED_BOS

        with patch("translate.remote_translate_batch") as remote, \
             patch("translate.load_model", side_effect=fake_load), \
             patch("translate.translate_batch", return_value=["Hello"]):
            result = translate_spans([cue("Merhaba")], [[0]], "tr", remote_url="http://media:8091",
                                     config=TranslationConfig(device="cpu"))
        self.assertEqual(result, ["Hello"])
        self.assertEqual(loaded, [MARIAN])
        remote.assert_not_called()

    def test_a_language_without_a_dedicated_model_loads_nllb(self):
        loaded = []

        def fake_load(config, code):
            loaded.append(config.repo)
            return object(), object(), 7

        with patch("translate.load_model", side_effect=fake_load), \
             patch("translate.translate_batch", return_value=["Hola"]):
            translate_spans([cue("Hola")], [[0]], "es", config=TranslationConfig(device="cpu"))
        self.assertEqual(loaded, [translate.NLLB_REPO])


if __name__ == "__main__":
    unittest.main()
