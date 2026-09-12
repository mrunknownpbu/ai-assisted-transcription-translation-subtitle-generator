"""translate_batch()'s chunking behaviour -- the real bug this guards
against: an earlier version sent an entire job's sentences (48, on a real
5-minute clip) to NLLB in one generate() call and hit a confirmed CUDA
OutOfMemoryError. torch itself isn't a host test dependency (see other
GPU-dependent modules' convention), so this tests the chunking/dispatch
logic by mocking _generate_one_batch, not the actual OOM-catch path
(verified separately against real hardware).
"""

import unittest
from unittest.mock import patch

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

    def test_default_batch_size_is_12(self):
        self.assertEqual(TranslationConfig().batch_size, 12)


if __name__ == "__main__":
    unittest.main()
