"""translate_batch()'s chunking behaviour -- the real bug this guards
against: an earlier version sent an entire job's sentences (48, on a real
5-minute clip) to NLLB in one generate() call and hit a confirmed CUDA
OutOfMemoryError. torch itself isn't a host test dependency (see other
GPU-dependent modules' convention), so this tests the chunking/dispatch
logic by mocking _generate_one_batch, not the actual OOM-catch path
(verified separately against real hardware).
"""

import contextlib
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

    def test_default_batch_size_is_8(self):
        # Lowered from 12 (2026-09-19, real S01E04 OOM -- see
        # TranslationConfig's docstring) for GPU-translation safety margin.
        self.assertEqual(TranslationConfig().batch_size, 8)

    def test_default_num_beams_is_2(self):
        # Halved from 4 (2026-09-19, same real OOM) for the same reason.
        self.assertEqual(TranslationConfig().num_beams, 2)


class GpuMemoryReleaseTests(unittest.TestCase):
    """Real production evidence (2026-09-19, a full episode on GPU
    translation): without a release after every chunk, PyTorch's caching
    allocator visibly grew across ~51 chunks of one translation stage
    (5442MiB -> 7530MiB on a 7680MiB card, 71MiB free at the low point)
    even though every chunk was the same shape. One free_gpu() per chunk
    keeps peak usage from climbing toward the card's ceiling."""

    def test_free_gpu_called_after_each_cuda_batch(self):
        sentences = [f"s{i}" for i in range(25)]
        with patch.object(translate, "_generate_one_batch", return_value=["t"]), \
             patch("gpu.free_gpu") as mock_free:
            translate_batch(None, None, 0, sentences, "cuda", TranslationConfig(batch_size=10))
        self.assertEqual(mock_free.call_count, 3)  # 10 + 10 + 5

    def test_free_gpu_not_called_for_cpu_translation(self):
        sentences = ["a", "b"]
        with patch.object(translate, "_generate_one_batch", return_value=["t"]), \
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
