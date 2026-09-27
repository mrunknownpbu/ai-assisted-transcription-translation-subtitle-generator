"""translate_batch()'s chunking behaviour -- the real bug this guards
against: an earlier version sent an entire job's sentences (48, on a real
5-minute clip) to NLLB in one generate() call and hit a confirmed CUDA
OutOfMemoryError. torch itself isn't a host test dependency (see other
GPU-dependent modules' convention), so this tests the chunking/dispatch
logic by mocking _generate_one_batch, not the actual OOM-catch path
(verified separately against real hardware).
"""

import contextlib
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import translate
from translate import TranslationConfig, translate_batch


class ChunkingTests(unittest.TestCase):
    def test_never_sends_more_than_batch_size_sentences_in_one_call(self):
        sentences = [f"sentence {i}" for i in range(30)]
        seen_batch_sizes = []

        def fake_generate(model, tok, bos, batch, device, config):
            seen_batch_sizes.append(len(batch))
            return [f"T({s})" for s in batch]

        with patch.object(translate, "_generate_one_batch", side_effect=fake_generate):
            result = translate_batch(None, None, 0, sentences, "cuda",
                                     TranslationConfig(batch_size=12))

        self.assertTrue(all(n <= 12 for n in seen_batch_sizes), seen_batch_sizes)
        self.assertEqual(sum(seen_batch_sizes), 30)
        self.assertEqual(len(seen_batch_sizes), 3)  # 12 + 12 + 6

    def test_preserves_order_and_count(self):
        sentences = [f"s{i}" for i in range(25)]

        def fake_generate(model, tok, bos, batch, device, config):
            return [f"T:{s}" for s in batch]

        with patch.object(translate, "_generate_one_batch", side_effect=fake_generate):
            result = translate_batch(None, None, 0, sentences, "cuda", TranslationConfig(batch_size=10))

        self.assertEqual(result, [f"T:s{i}" for i in range(25)])

    def test_empty_input_no_calls(self):
        with patch.object(translate, "_generate_one_batch") as mock_gen:
            result = translate_batch(None, None, 0, [], "cuda", TranslationConfig())
        self.assertEqual(result, [])
        mock_gen.assert_not_called()

    def test_batches_are_length_sorted_and_results_restored(self):
        # Longest-first batching cuts padding waste (bench: 147s -> 45s on a
        # real 2,678-sentence episode, together with the dedicated
        # profile); the caller must still get results in input order.
        sentences = ["a", "cccccc", "bb", "dddddddd", "e"]
        seen = []

        def fake_generate(model, tok, bos, batch, device, config):
            seen.append(list(batch))
            return [f"T:{s}" for s in batch]

        with patch.object(translate, "_generate_one_batch", side_effect=fake_generate):
            result = translate_batch(None, None, 0, sentences, "cpu", TranslationConfig(), batch_size=2)

        self.assertEqual(seen, [["dddddddd", "cccccc"], ["bb", "a"], ["e"]])
        self.assertEqual(result, [f"T:{s}" for s in sentences])

    def test_progress_reports_cumulative_done(self):
        progress = []
        with patch.object(translate, "_generate_one_batch", side_effect=lambda *a: ["t"] * len(a[3])):
            translate_batch(None, None, 0, ["x"] * 5, "cpu", TranslationConfig(), batch_size=2,
                            on_progress=lambda d, t: progress.append((d, t)))
        self.assertEqual(progress, [(2, 5), (4, 5), (5, 5)])


class GpuProfileDefaultsTests(unittest.TestCase):
    """SHARED keeps the 2026-09-19 Tdarr-contention values (real S01E04
    OOM on the P4 -- see TranslationConfig's comment); DEDICATED is the
    current host's measured sweet spot (scripts/bench_translate.py)."""

    def _config(self, **env):
        with patch.dict("os.environ", env, clear=False):
            for key in ("SUBTITLE_AI_GPU_SHARED", "SUBTITLE_AI_NLLB_BATCH_SIZE",
                        "SUBTITLE_AI_NLLB_NUM_BEAMS"):
                if key not in env:
                    os.environ.pop(key, None)
            return TranslationConfig()

    def test_dedicated_is_the_default(self):
        config = self._config()
        self.assertEqual((config.batch_size, config.num_beams),
                         (translate.DEDICATED_BATCH_SIZE, translate.DEDICATED_NUM_BEAMS))
        self.assertEqual((config.batch_size, config.num_beams), (32, 2))

    def test_shared_profile_keeps_contention_safe_values(self):
        config = self._config(SUBTITLE_AI_GPU_SHARED="1")
        self.assertEqual((config.batch_size, config.num_beams), (8, 2))

    def test_env_overrides_win_over_profile(self):
        config = self._config(SUBTITLE_AI_GPU_SHARED="1", SUBTITLE_AI_NLLB_BATCH_SIZE="64",
                              SUBTITLE_AI_NLLB_NUM_BEAMS="4")
        self.assertEqual((config.batch_size, config.num_beams), (64, 4))

    def test_blank_or_invalid_override_falls_back(self):
        for raw in ("", "zero", "0", "-3"):
            with self.subTest(raw=raw):
                self.assertEqual(self._config(SUBTITLE_AI_NLLB_BATCH_SIZE=raw).batch_size, 32)

    def test_explicit_constructor_values_still_win(self):
        self.assertEqual(TranslationConfig(batch_size=5).batch_size, 5)


class GpuMemoryReleaseTests(unittest.TestCase):
    """Real production evidence (2026-09-19, a full episode on the SHARED
    Tesla P4): without a release after every chunk, PyTorch's caching
    allocator grew across ~51 chunks of one translation stage (5442MiB ->
    7530MiB on a 7680MiB card, 71MiB free at the low point) while Tdarr
    competed for the same card. So the per-chunk free_gpu() runs only on
    a shared GPU; on a dedicated one it measured ~20% slower for nothing."""

    def test_free_gpu_called_after_each_cuda_batch_when_shared(self):
        sentences = [f"s{i}" for i in range(25)]
        with patch.dict("os.environ", {"SUBTITLE_AI_GPU_SHARED": "1"}), \
             patch.object(translate, "_generate_one_batch", return_value=["t"]), \
             patch("gpu.free_gpu") as mock_free:
            translate_batch(None, None, 0, sentences, "cuda", TranslationConfig(), batch_size=10)
        self.assertEqual(mock_free.call_count, 3)  # 10 + 10 + 5

    def test_free_gpu_not_called_per_batch_on_dedicated_gpu(self):
        sentences = [f"s{i}" for i in range(25)]
        with patch.dict("os.environ", {"SUBTITLE_AI_GPU_SHARED": ""}), \
             patch.object(translate, "_generate_one_batch", return_value=["t"]), \
             patch("gpu.free_gpu") as mock_free:
            translate_batch(None, None, 0, sentences, "cuda", TranslationConfig(), batch_size=10)
        mock_free.assert_not_called()

    def test_free_gpu_not_called_for_cpu_translation(self):
        sentences = ["a", "b"]
        with patch.dict("os.environ", {"SUBTITLE_AI_GPU_SHARED": "1"}), \
             patch.object(translate, "_generate_one_batch", return_value=["t"]), \
             patch("gpu.free_gpu") as mock_free:
            translate_batch(None, None, 0, sentences, "cpu", TranslationConfig(batch_size=10))
        mock_free.assert_not_called()


class FakeEncoding(dict):
    def to(self, device):
        return self


class RepetitionGuardTests(unittest.TestCase):
    """The real bug this guards against: NLLB's generate() call had no
    repetition guard, and a source span with natural internal repetition
    (a real Japanese "zombie" span from multi-language validation) produced
    ~13 near-duplicate translated clauses instead of one sentence. torch
    isn't a host test dependency (see module docstring), so this proves the
    guard is actually wired into the real model.generate() call -- by
    faking torch/model/tok rather than mocking _generate_one_batch away
    entirely -- not that it suppresses real degenerate output (verified
    separately against real hardware: the zombie span collapsed from a
    13x-repeated clause to one clean sentence, while the Turkish emphasis
    cases "Tamam tamam..." and "Eda! Eda!..." came back byte-identical to
    their pre-fix translations, since a legitimate repeat is only a 1-2
    word span repeated once, well under the 4-token guard)."""

    def test_default_no_repeat_ngram_size_is_4(self):
        self.assertEqual(TranslationConfig().no_repeat_ngram_size, 4)

    def test_generate_call_receives_configured_no_repeat_ngram_size(self):
        fake_torch = types.ModuleType("torch")
        fake_torch.inference_mode = contextlib.nullcontext
        fake_torch.cuda = types.SimpleNamespace(OutOfMemoryError=RuntimeError)

        model = MagicMock()
        model.generate.return_value = "GEN_TOKENS"
        tok = MagicMock()
        tok.return_value = FakeEncoding(input_ids="IDS", attention_mask="MASK")
        tok.batch_decode.return_value = ["translated"]

        with patch.dict(sys.modules, {"torch": fake_torch}):
            result = translate._generate_one_batch(
                model, tok, 0, ["hello"], "cuda", TranslationConfig())

        self.assertEqual(result, ["translated"])
        _, kwargs = model.generate.call_args
        self.assertEqual(kwargs["no_repeat_ngram_size"], 4)


class DecodeSpacingTests(unittest.TestCase):
    """Real bug (2026-09-22, confirmed in a committed .en.srt: "That 's
    nice .", "go ."): this tokenizer's own clean_up_tokenization_spaces
    default (transformers 4.48) is False unless the decode call passes it
    explicitly, so NLLB's raw subword spacing survived into output. Not
    the same defect as the Title Case source-casing issue (asr.py's
    hotwords fix) -- confirmed still reproducing on sentence-case input."""

    def _decode(self):
        fake_torch = types.ModuleType("torch")
        fake_torch.inference_mode = contextlib.nullcontext
        fake_torch.cuda = types.SimpleNamespace(OutOfMemoryError=RuntimeError)
        model = MagicMock()
        model.generate.return_value = "GEN_TOKENS"
        tok = MagicMock()
        tok.return_value = FakeEncoding(input_ids="IDS", attention_mask="MASK")
        tok.batch_decode.return_value = ["That's nice."]
        with patch.dict(sys.modules, {"torch": fake_torch}):
            translate._generate_one_batch(model, tok, 0, ["Güzelmiş."], "cuda", TranslationConfig())
        return tok

    def test_batch_decode_receives_clean_up_tokenization_spaces_true(self):
        tok = self._decode()
        _, kwargs = tok.batch_decode.call_args
        self.assertTrue(kwargs.get("clean_up_tokenization_spaces"))

    def test_skip_special_tokens_still_requested(self):
        tok = self._decode()
        _, kwargs = tok.batch_decode.call_args
        self.assertTrue(kwargs.get("skip_special_tokens"))


if __name__ == "__main__":
    unittest.main()


class FakeCt2Result:
    def __init__(self, tokens):
        self.hypotheses = [tokens]


class FakeTokenizer:
    """Just enough of the NLLB tokenizer for the CTranslate2 path."""

    def __call__(self, batch, truncation=True, max_length=512):
        return {"input_ids": [[1] + [len(w) for w in s.split()] + [2] for s in batch]}

    def convert_ids_to_tokens(self, ids):
        return [f"t{i}" for i in ids]

    def convert_tokens_to_ids(self, tokens):
        return tokens

    def decode(self, ids, skip_special_tokens, clean_up_tokenization_spaces):
        assert skip_special_tokens and clean_up_tokenization_spaces
        return " ".join(ids)


class Ct2BackendTests(unittest.TestCase):
    """SUBTITLE_AI_NLLB_BACKEND=ct2 (B3, 2026-09-28): same tokenizer, beams,
    length cap, no_repeat_ngram_size and decode flags as the HF path, and
    the same halve-and-retry on OOM."""

    def config(self, **kw):
        return TranslationConfig(backend="ct2", batch_size=32, num_beams=2, **kw)

    def test_passes_generation_settings_and_strips_the_target_prefix(self):
        translator = MagicMock()
        translator.translate_batch.side_effect = lambda src, **kw: [
            FakeCt2Result(["eng_Latn", "Hello", "there"]) for _ in src]
        out = translate._generate_one_batch(translator, FakeTokenizer(), "eng_Latn", ["Merhaba"],
                                            "cuda", self.config())
        self.assertEqual(out, ["Hello there"])
        kwargs = translator.translate_batch.call_args.kwargs
        self.assertEqual(kwargs["target_prefix"], [["eng_Latn"]])
        self.assertEqual(kwargs["beam_size"], 2)
        self.assertEqual(kwargs["max_decoding_length"], 256)
        self.assertEqual(kwargs["no_repeat_ngram_size"], 4)
        self.assertEqual(translator.translate_batch.call_args.args[0], [["t1", "t7", "t2"]])

    def test_oom_halves_the_batch(self):
        calls = []

        def fake(src, **kw):
            calls.append(len(src))
            if len(src) > 1:
                raise RuntimeError("CUDA failed with error out of memory")
            return [FakeCt2Result(["eng_Latn", "x"])]

        translator = MagicMock()
        translator.translate_batch.side_effect = fake
        out = translate._generate_one_batch(translator, FakeTokenizer(), "eng_Latn", ["a", "b"],
                                            "cuda", self.config())
        self.assertEqual(out, ["x", "x"])
        self.assertEqual(calls, [2, 1, 1])

    def test_other_runtime_errors_are_not_swallowed(self):
        translator = MagicMock()
        translator.translate_batch.side_effect = RuntimeError("unsupported model")
        with self.assertRaises(RuntimeError):
            translate._generate_one_batch(translator, FakeTokenizer(), "eng_Latn", ["a", "b"],
                                          "cuda", self.config())

    def test_backend_env_default_and_validation(self):
        for raw, expected in (("", "hf"), ("ct2", "ct2"), ("CT2", "ct2"), ("vllm", "hf")):
            with self.subTest(raw=raw), patch.dict("os.environ", {"SUBTITLE_AI_NLLB_BACKEND": raw}):
                self.assertEqual(TranslationConfig().backend, expected)

    def test_ct2_path_env_override(self):
        with patch.dict("os.environ", {"SUBTITLE_AI_NLLB_CT2_PATH": "/models/other"}):
            self.assertEqual(TranslationConfig().ct2_path, "/models/other")
        with patch.dict("os.environ", {"SUBTITLE_AI_NLLB_CT2_PATH": ""}):
            self.assertEqual(TranslationConfig().ct2_path, translate.DEFAULT_CT2_PATH)
