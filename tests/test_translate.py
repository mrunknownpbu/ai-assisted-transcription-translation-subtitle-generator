import unittest
from unittest.mock import patch

from transcript import BoundaryReason, Segment, Word
from translate import TranslationConfig, build_context_spans, translate_spans


def cue(index, start, end, text, boundary=None):
    words = [Word(text=w, original_text=w, start=start, end=end) for w in text.split()]
    return Segment(index=index, start=start, end=end, words=words, avg_logprob=0.0,
                  no_speech_prob=0.0, compression_ratio=0.0, boundary_before=boundary)


class BuildContextSpansTests(unittest.TestCase):
    def test_real_acoustic_gap_forces_new_span_even_under_max_gap(self):
        cues = [cue(0, 107.843, 108.843, "Tamam"),
               cue(1, 109.748, 110.5, "Eda Eda", boundary=BoundaryReason.REAL_ACOUSTIC_GAP)]
        spans = build_context_spans(cues)
        self.assertEqual(spans, [[0], [1]])

    def test_display_boundary_does_not_force_a_new_span(self):
        cues = [cue(0, 0.0, 1.0, "a"), cue(1, 1.2, 2.0, "b", boundary=BoundaryReason.DISPLAY_SPLIT)]
        spans = build_context_spans(cues)
        self.assertEqual(spans, [[0, 1]])

    def test_sentence_end_closes_a_span(self):
        cues = [cue(0, 0.0, 1.0, "Hello there."), cue(1, 1.1, 2.0, "How are you?")]
        spans = build_context_spans(cues)
        self.assertEqual(spans, [[0], [1]])

    def test_large_gap_without_provenance_still_forces_a_break(self):
        cues = [cue(0, 0.0, 1.0, "Tamam kalktim"), cue(1, 10.0, 11.0, "Gunaydin dunya")]
        spans = build_context_spans(cues)
        self.assertEqual(spans, [[0], [1]])

    def test_ordinary_dialogue_pause_does_not_break_a_span(self):
        cues = [cue(0, 0.0, 1.0, "Eda"), cue(1, 1.8, 2.5, "neredesin")]
        spans = build_context_spans(cues)
        self.assertEqual(spans, [[0, 1]])


class TranslateSpansGpuLockTests(unittest.TestCase):
    """gpu_lock() serializes GPU contention (see gpu.py's module
    docstring) -- real defect (2026-09-17): it used to be acquired
    unconditionally whenever this call owns model construction, even for
    CPU-only translation, needlessly blocking the Analyze endpoint's
    stream-sampler (a real GPU consumer) for no protective reason."""

    def _run(self, device):
        cues = [cue(0, 0.0, 1.0, "Merhaba")]
        spans = [[0]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch", return_value=["Hello"]), \
             patch("gpu.gpu_lock") as mock_lock:
            translate_spans(cues, spans, "tr", config=TranslationConfig(device=device))
        return mock_lock

    def test_cpu_translation_does_not_take_the_gpu_lock(self):
        self._run("cpu").assert_not_called()

    def test_cuda_translation_still_takes_the_gpu_lock(self):
        self._run("cuda").assert_called_once()


class PhraseMapShortCircuitTests(unittest.TestCase):
    """phrase_map (added 2026-09-19, see glossary.PhraseEntry) skips the
    model entirely for a span whose whole text exact-matches a known
    phrase -- fixes real hallucination on short, context-free utterances
    (e.g. "Bekle." -> "Wait, wait, wait. I got it.")."""

    def test_phrase_match_skips_the_model_and_uses_forced_translation(self):
        cues = [cue(0, 0.0, 1.0, "Peki.")]
        spans = [[0]]
        with patch("translate.load_model") as mock_load, \
             patch("translate.translate_batch") as mock_batch:
            result = translate_spans(cues, spans, "tr", phrase_map={"peki": "Okay."})
        mock_load.assert_not_called()
        mock_batch.assert_not_called()
        self.assertEqual(result, ["Okay."])

    def test_phrase_match_is_spliced_back_at_the_correct_index(self):
        cues = [cue(0, 0.0, 1.0, "Peki."), cue(1, 1.0, 2.0, "Merhaba nasılsın")]
        spans = [[0], [1]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch", return_value=["Hello, how are you"]) as mock_batch:
            result = translate_spans(cues, spans, "tr", phrase_map={"peki": "Okay."})
        # Only the non-matched span's text is ever sent to the model.
        mock_batch.assert_called_once()
        self.assertEqual(mock_batch.call_args[0][3], ["Merhaba nasılsın"])
        self.assertEqual(result, ["Okay.", "Hello, how are you"])

    def test_no_phrase_map_behaves_exactly_as_before(self):
        cues = [cue(0, 0.0, 1.0, "Merhaba")]
        spans = [[0]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch", return_value=["Hello"]):
            result = translate_spans(cues, spans, "tr")
        self.assertEqual(result, ["Hello"])


class BareEntityShortCircuitTests(unittest.TestCase):
    """Real evidence (S01E02 QC run, 2026-09-20): bare "Cenk." (Cenk
    already protected) hallucinated to "Cenk, what's going on?" under
    NLLB. A span that entity-protection reduces to nothing but a
    placeholder plus punctuation must skip the model entirely."""

    def test_bare_entity_mention_skips_the_model(self):
        from glossary import Entity, build_glossary
        g = build_glossary([Entity("Cenk", ["Cenk"])])
        cues = [cue(0, 0.0, 1.0, "Cenk.")]
        spans = [[0]]
        with patch("translate.load_model") as mock_load, \
             patch("translate.translate_batch") as mock_batch:
            result = translate_spans(cues, spans, "tr", glossary_map=g)
        mock_load.assert_not_called()
        mock_batch.assert_not_called()
        self.assertEqual(result, ["Cenk."])

    def test_bare_entity_mention_spliced_back_at_correct_index(self):
        from glossary import Entity, build_glossary
        g = build_glossary([Entity("Cenk", ["Cenk"])])
        cues = [cue(0, 0.0, 1.0, "Cenk."), cue(1, 1.0, 2.0, "Cenk'in haberi var mı?")]
        spans = [[0], [1]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch", return_value=["Xaa's news, is there?"]) as mock_batch:
            result = translate_spans(cues, spans, "tr", glossary_map=g)
        # Only the non-bare span's protected text is ever sent to the model.
        mock_batch.assert_called_once()
        self.assertEqual(mock_batch.call_args[0][3], ["Xaa'in haberi var mı?"])
        self.assertEqual(result, ["Cenk.", "Cenk's news, is there?"])

    def test_real_sentence_with_protected_name_still_goes_through_the_model(self):
        from glossary import Entity, build_glossary
        g = build_glossary([Entity("Cenk", ["Cenk"])])
        cues = [cue(0, 0.0, 1.0, "Cenk'in haberi var mı?")]
        spans = [[0]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch", return_value=["Xaa's news, is there?"]) as mock_batch:
            result = translate_spans(cues, spans, "tr", glossary_map=g)
        mock_batch.assert_called_once()
        self.assertEqual(result, ["Cenk's news, is there?"])


class DefaultConfigTests(unittest.TestCase):
    def test_default_device_is_cuda(self):
        # Flipped this session (real nvidia-smi headroom measured on a
        # live job: 3972MiB free of 7680MiB total with ASR + Tdarr both
        # active, comfortably above NLLB-200-distilled-1.3B's ~2.6GB
        # fp16 footprint) -- see translate.py's TranslationConfig
        # docstring for the full reasoning and accepted tradeoff.
        self.assertEqual(TranslationConfig().device, "cuda")


if __name__ == "__main__":
    unittest.main()
