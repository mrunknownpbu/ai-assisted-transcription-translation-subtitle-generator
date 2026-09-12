import unittest

from transcript import ModelInfo, cache_key


class CacheKeyLanguageTests(unittest.TestCase):
    """cache_key() is not yet wired into any reuse layer (nothing in the
    pipeline currently loads a cached transcript), but it exists as the
    utility that any future one would use -- these tests protect its one
    correctness requirement: two different resolved languages for the
    SAME file must never hash to the same key."""

    def setUp(self):
        self.asr_model = ModelInfo(name="faster-whisper-large-v3", version="large-v3")

    def test_different_languages_produce_different_keys(self):
        key_tr = cache_key("hash", 0, "aac", "tr", self.asr_model, None, "2.0.0")
        key_ja = cache_key("hash", 0, "aac", "ja", self.asr_model, None, "2.0.0")
        self.assertNotEqual(key_tr, key_ja)

    def test_same_inputs_are_deterministic(self):
        key_a = cache_key("hash", 0, "aac", "tr", self.asr_model, None, "2.0.0")
        key_b = cache_key("hash", 0, "aac", "tr", self.asr_model, None, "2.0.0")
        self.assertEqual(key_a, key_b)

    def test_different_stream_index_produces_different_key(self):
        key_0 = cache_key("hash", 0, "aac", "tr", self.asr_model, None, "2.0.0")
        key_1 = cache_key("hash", 1, "aac", "tr", self.asr_model, None, "2.0.0")
        self.assertNotEqual(key_0, key_1)

    def test_different_stream_codec_produces_different_key(self):
        key_aac = cache_key("hash", 0, "aac", "tr", self.asr_model, None, "2.0.0")
        key_opus = cache_key("hash", 0, "opus", "tr", self.asr_model, None, "2.0.0")
        self.assertNotEqual(key_aac, key_opus)


if __name__ == "__main__":
    unittest.main()
