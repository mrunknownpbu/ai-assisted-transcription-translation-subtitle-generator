import types
import unittest
from unittest.mock import MagicMock, Mock, patch

import httpx

from transcript import BoundaryReason, Segment, Word
from glossary import Entity, build_glossary, protect
from translate import (RemoteTranslationError, TranslationConfig,
                       build_context_spans, orphan_context_padding_enabled,
                       remote_translate_batch, translate_spans, _chunk,
                       _find_orphan_spans, _prefer_chunked, _strip_anchor_affix)


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

    def test_corrupted_placeholder_from_the_model_is_repaired_before_restore(self):
        # Real evidence (S01E05 QC, 2026-09-20): the model returned a
        # one-character-corrupted placeholder ("Xax" for real "Xac"),
        # which used to leak into the final subtitle untouched.
        from glossary import Entity, build_glossary
        g = build_glossary([Entity("Serkan", ["Serkan"])])
        cues = [cue(0, 0.0, 1.0, "Serkan'ı nasıl kıskandığını.")]
        spans = [[0]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch", return_value=["How jealous she was of Xab."]):
            result = translate_spans(cues, spans, "tr", glossary_map=g)
        self.assertEqual(result, ["How jealous she was of Serkan."])


class RunOnChunkRetryTests(unittest.TestCase):
    """Real bug (S01E03, 2026-09-21): a source cue with zero
    sentence-ending punctuation (glossary.is_unpunctuated_run_on())
    gets a bounded chunk-and-compare retry -- see translate.py's
    _apply_chunk_retry()/_prefer_chunked() docstrings. Validated
    2026-09-21 against 1,157 real flagged S01 cues mentioning a
    protected entity: 227 (19.6%) preferred the chunked candidate,
    manually sampled with zero regressions."""

    RUN_ON = ("Sen Kahveni İçerken Ben Hazırladım Tamam Alptekin Amca "
             "Ay Selinciğim Biz Amca Değil")

    def test_chunked_candidate_wins_when_it_preserves_a_dropped_entity(self):
        from glossary import Entity, build_glossary
        g = build_glossary([Entity("Alptekin", ["Alptekin"])])
        cues = [cue(0, 0.0, 10.0, self.RUN_ON)]
        spans = [[0]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch",
                   side_effect=[
                       ["I'm sure of it."],  # primary: drops Alptekin entirely
                       ["I made it while you had your coffee.",
                        "Uncle Xaa, oh dear Selin.",
                        "We're not."],       # chunked: keeps the placeholder
                   ]) as mock_batch:
            result = translate_spans(cues, spans, "tr", glossary_map=g)
        self.assertEqual(mock_batch.call_count, 2)
        self.assertEqual(mock_batch.call_args_list[1][0][3],
                        ["Sen Kahveni İçerken Ben Hazırladım Tamam.",
                         "Xaa Amca Ay Selinciğim Biz Amca.",
                         "Değil."])
        self.assertIn("Alptekin", result[0])

    def test_original_kept_when_chunked_candidate_is_worse(self):
        from glossary import Entity, build_glossary
        g = build_glossary([Entity("Alptekin", ["Alptekin"])])
        cues = [cue(0, 0.0, 10.0, self.RUN_ON)]
        spans = [[0]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch",
                   side_effect=[
                       ["Uncle Xaa was very upset that morning."],  # keeps it
                       ["I made coffee.", "Good morning.", "Fine."],  # loses it
                   ]) as mock_batch:
            result = translate_spans(cues, spans, "tr", glossary_map=g)
        self.assertEqual(mock_batch.call_count, 2)
        self.assertEqual(result[0], "Uncle Alptekin was very upset that morning.")

    def test_no_protected_entity_keeps_original_on_tie(self):
        # No glossary at all -- only the length-ratio fallback signal
        # exists, and neither candidate here is "suspiciously short", so
        # the tie keeps the original untouched.
        cues = [cue(0, 0.0, 10.0, self.RUN_ON)]
        spans = [[0]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch",
                   side_effect=[
                       ["He made coffee while she got ready this morning."],
                       ["He made the coffee.", "She got ready.", "That morning."],
                   ]) as mock_batch:
            result = translate_spans(cues, spans, "tr")
        self.assertEqual(mock_batch.call_count, 2)
        self.assertEqual(result[0], "He made coffee while she got ready this morning.")

    def test_ordinary_punctuated_sentence_never_triggers_a_retry(self):
        cues = [cue(0, 0.0, 5.0, "Bu adam gercekten cok tuhaf davraniyor bu aralar sanki.")]
        spans = [[0]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch",
                   return_value=["This guy has been acting really weird lately."]) as mock_batch:
            translate_spans(cues, spans, "tr")
        mock_batch.assert_called_once()


class DefaultConfigTests(unittest.TestCase):
    def test_default_device_is_cuda(self):
        # Flipped this session (real nvidia-smi headroom measured on a
        # live job: 3972MiB free of 7680MiB total with ASR + Tdarr both
        # active, comfortably above NLLB-200-distilled-1.3B's ~2.6GB
        # fp16 footprint) -- see translate.py's TranslationConfig
        # docstring for the full reasoning and accepted tradeoff.
        self.assertEqual(TranslationConfig().device, "cuda")


def _http_response(status=200, json_body=None):
    resp = Mock()
    resp.status_code = status
    resp.json.return_value = json_body if json_body is not None else {}
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            str(status), request=Mock(), response=resp)
    else:
        resp.raise_for_status.side_effect = None
    return resp


def _mock_httpx_client(post_side_effect):
    """`httpx.Client(...)` is imported lazily inside remote_translate_batch,
    so patching the real `httpx.Client` (not `translate.httpx.Client`,
    which doesn't exist at module level) affects it correctly -- Python
    caches modules in sys.modules, so a lazy `import httpx` still sees
    this same patched attribute."""
    client_instance = MagicMock()
    client_instance.post.side_effect = post_side_effect
    client_cm = MagicMock()
    client_cm.__enter__.return_value = client_instance
    client_cm.__exit__.return_value = False
    return client_cm


class RemoteTranslateBatchTests(unittest.TestCase):
    """Real motivation (2026-09-20 benchmark): an RTX 3070 on the media
    server measured ~8x this deployment's usual Tesla P4 throughput on
    the same model/config/sentences -- remote_translate_batch() is the
    HTTP client half of routing translation there."""

    def test_successful_batch_returns_translations_and_reports_progress(self):
        responses = [_http_response(200, {"translations": ["Hello", "World"]})]
        with patch("httpx.Client", return_value=_mock_httpx_client(responses)):
            progress = []
            result = remote_translate_batch("http://media:8091", ["Merhaba", "Dunya"], "tr",
                                            batch_size=8, on_progress=lambda d, t: progress.append((d, t)))
        self.assertEqual(result, ["Hello", "World"])
        self.assertEqual(progress, [(2, 2)])

    def test_chunks_by_batch_size(self):
        responses = [_http_response(200, {"translations": ["a"]}),
                    _http_response(200, {"translations": ["b"]})]
        with patch("httpx.Client", return_value=_mock_httpx_client(responses)) as mock_client_cls:
            result = remote_translate_batch("http://media:8091", ["x", "y"], "tr", batch_size=1)
        self.assertEqual(result, ["a", "b"])
        client_instance = mock_client_cls.return_value.__enter__.return_value
        self.assertEqual(client_instance.post.call_count, 2)

    def test_connection_error_raises_remote_translation_error(self):
        with patch("httpx.Client", return_value=_mock_httpx_client(httpx.ConnectError("down"))):
            with self.assertRaises(RemoteTranslationError):
                remote_translate_batch("http://media:8091", ["Merhaba"], "tr", batch_size=8)

    def test_non_2xx_status_raises_remote_translation_error(self):
        with patch("httpx.Client", return_value=_mock_httpx_client([_http_response(500)])):
            with self.assertRaises(RemoteTranslationError):
                remote_translate_batch("http://media:8091", ["Merhaba"], "tr", batch_size=8)

    def test_malformed_response_raises_remote_translation_error(self):
        with patch("httpx.Client", return_value=_mock_httpx_client([_http_response(200, {"oops": []})])):
            with self.assertRaises(RemoteTranslationError):
                remote_translate_batch("http://media:8091", ["Merhaba"], "tr", batch_size=8)

    def test_empty_sentences_makes_no_http_call(self):
        with patch("httpx.Client") as mock_client_cls:
            result = remote_translate_batch("http://media:8091", [], "tr", batch_size=8)
        self.assertEqual(result, [])
        mock_client_cls.assert_not_called()


class TranslateSpansRemoteTests(unittest.TestCase):
    """translate_spans()'s remote_url wiring: try remote first, fall back
    to the exact local path on RemoteTranslationError."""

    def test_remote_success_skips_local_model_entirely(self):
        cues = [cue(0, 0.0, 1.0, "Merhaba nasılsın")]
        spans = [[0]]
        with patch("translate.remote_translate_batch", return_value=["Hello, how are you"]) as mock_remote, \
             patch("translate.load_model") as mock_load, \
             patch("translate.translate_batch") as mock_batch:
            result = translate_spans(cues, spans, "tr", remote_url="http://media:8091")
        mock_remote.assert_called_once()
        mock_load.assert_not_called()
        mock_batch.assert_not_called()
        self.assertEqual(result, ["Hello, how are you"])

    def test_remote_failure_falls_back_to_local(self):
        cues = [cue(0, 0.0, 1.0, "Merhaba nasılsın")]
        spans = [[0]]
        with patch("translate.remote_translate_batch", side_effect=RemoteTranslationError("down")), \
             patch("translate.load_model", return_value=(object(), object(), 0)) as mock_load, \
             patch("translate.translate_batch", return_value=["Hello, how are you"]) as mock_batch:
            result = translate_spans(cues, spans, "tr", remote_url="http://media:8091")
        mock_load.assert_called_once()
        mock_batch.assert_called_once()
        self.assertEqual(result, ["Hello, how are you"])

    def test_no_remote_url_never_calls_remote(self):
        cues = [cue(0, 0.0, 1.0, "Merhaba")]
        spans = [[0]]
        with patch("translate.remote_translate_batch") as mock_remote, \
             patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch", return_value=["Hello"]):
            result = translate_spans(cues, spans, "tr")
        mock_remote.assert_not_called()
        self.assertEqual(result, ["Hello"])


class MultiSpeakerDashCueTests(unittest.TestCase):
    """Full translate_spans() integration test using the exact real
    source text that produced real broken output in production
    (S01E01 QC, 2026-09-20) -- see
    glossary.split_multi_speaker_dash_lines()'s docstring. Uses a plain
    SimpleNamespace(text=...) rather than the `cue()` helper above,
    since `cue()` builds a word-list Segment (matching the video/ASR
    path) whose reconstructed .text wouldn't preserve the embedded
    newline the way srt_translation.py's ValidatedCue.text does."""

    def test_identical_dash_lines_translated_independently_and_rejoined(self):
        cues = [types.SimpleNamespace(text="- Günaydın Serkan Bey.\n- Günaydın Serkan Bey.")]
        spans = [[0]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch",
                   return_value=["Good morning, Mr. Serkan.", "Good morning, Mr. Serkan."]) as mock_batch:
            result = translate_spans(cues, spans, "tr")
        self.assertEqual(mock_batch.call_args[0][3],
                        ["Günaydın Serkan Bey.", "Günaydın Serkan Bey."])
        self.assertEqual(result, ["- Good morning, Mr. Serkan.\n- Good morning, Mr. Serkan."])

    def test_different_dash_lines_translated_independently_and_rejoined(self):
        cues = [types.SimpleNamespace(text="-Evren Bey oradaki adam.\n-Hangisi?")]
        spans = [[0]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch",
                   return_value=["Mr. Evren, the man over there.", "Which one?"]) as mock_batch:
            result = translate_spans(cues, spans, "tr")
        self.assertEqual(mock_batch.call_args[0][3],
                        ["Evren Bey oradaki adam.", "Hangisi?"])
        self.assertEqual(result, ["- Mr. Evren, the man over there.\n- Which one?"])

    def test_ordinary_single_speaker_span_unaffected(self):
        cues = [types.SimpleNamespace(text="Merhaba nasılsın")]
        spans = [[0]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch", return_value=["Hello, how are you"]) as mock_batch:
            result = translate_spans(cues, spans, "tr")
        self.assertEqual(mock_batch.call_args[0][3], ["Merhaba nasılsın"])
        self.assertEqual(result, ["Hello, how are you"])


class MultiSentenceSpanTests(unittest.TestCase):
    """Full translate_spans() integration test using the exact real
    source text that produced real content-dropping truncation in
    production (Season 01 full-batch QC, 2026-09-20) -- see
    glossary.split_into_sentences()'s docstring."""

    def test_two_sentence_span_translated_independently_and_rejoined(self):
        cues = [types.SimpleNamespace(text="Senin için çok seviniyorum. İtalya sana çok iyi gelecek.")]
        spans = [[0]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch",
                   return_value=["I'm so happy for you.", "Italy will be very good for you."]) as mock_batch:
            result = translate_spans(cues, spans, "tr")
        self.assertEqual(mock_batch.call_args[0][3],
                        ["Senin için çok seviniyorum.", "İtalya sana çok iyi gelecek."])
        self.assertEqual(result, ["I'm so happy for you. Italy will be very good for you."])

    def test_multi_sentence_dash_line_nests_with_dash_split(self):
        # A dash line that ITSELF contains 2 sentences (each 2+ words, so
        # split_into_sentences' word-count guard doesn't hold it back):
        # both expansions must compose (sentence -> dash-line -> atomic
        # sentence) and rejoin in the right order.
        cues = [types.SimpleNamespace(
            text="- Ben gidiyorum. Yarın dönerim ben.\n- Tamam, görüşürüz.")]
        spans = [[0]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch",
                   return_value=["I'm leaving.", "I'll be back tomorrow.", "Okay, see you."]) as mock_batch:
            result = translate_spans(cues, spans, "tr")
        self.assertEqual(mock_batch.call_args[0][3],
                        ["Ben gidiyorum.", "Yarın dönerim ben.", "Tamam, görüşürüz."])
        self.assertEqual(result, ["- I'm leaving. I'll be back tomorrow.\n- Okay, see you."])

    def test_single_word_dash_lines_unaffected_by_sentence_split(self):
        # Regression guard: a short one-word-per-speaker dash exchange
        # must not be additionally exploded by sentence-splitting on top
        # of the existing dash-line split.
        cues = [types.SimpleNamespace(text="-Hı.\n-Hı.")]
        spans = [[0]]
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch", return_value=["Uh-huh.", "Uh-huh."]) as mock_batch:
            result = translate_spans(cues, spans, "tr")
        self.assertEqual(mock_batch.call_args[0][3], ["Hı.", "Hı."])
        self.assertEqual(result, ["- Uh-huh.\n- Uh-huh."])


class ChunkTests(unittest.TestCase):
    def test_splits_into_fixed_size_word_groups_with_trailing_period(self):
        text = "Sen Kahveni İçerken Ben Hazırladım Tamam Alptekin Amca Ay Selinciğim Biz Amca Değil"
        chunks = _chunk(text)
        self.assertEqual(chunks,
                        ["Sen Kahveni İçerken Ben Hazırladım Tamam.",
                         "Alptekin Amca Ay Selinciğim Biz Amca.",
                         "Değil."])

    def test_never_splits_a_placeholder_token(self):
        # Every glossary placeholder (e.g. "Xac") is exactly one
        # whitespace-delimited token, so word-boundary chunking can never
        # cut one in half.
        text = "Bir gün Xac ile birlikte cok uzun bir yolculuga ciktik"
        chunks = _chunk(text)
        self.assertTrue(any("Xac" in c for c in chunks))
        self.assertTrue(all("Xa" not in c or "Xac" in c for c in chunks))


class PreferChunkedTests(unittest.TestCase):
    def setUp(self):
        self.glossary_map = build_glossary([Entity("Alptekin", ["Alptekin"])])

    def test_prefers_chunked_when_it_preserves_a_dropped_entity(self):
        source_protected = protect("Alptekin Amca cok kizgindi o gun oyle degil miydi", self.glossary_map)
        original = "He was very angry that day, wasn't he?"  # drops Alptekin
        chunked = "Uncle Alptekin was very angry that day."   # keeps Alptekin
        self.assertTrue(_prefer_chunked(original, chunked, source_protected, self.glossary_map))

    def test_keeps_original_when_neither_differs_on_entity_preservation(self):
        source_protected = protect("Alptekin Amca cok kizgindi o gun oyle degil miydi", self.glossary_map)
        original = "Uncle Alptekin was very angry that day."
        chunked = "Uncle Alptekin seemed upset that day, right."
        self.assertFalse(_prefer_chunked(original, chunked, source_protected, self.glossary_map))

    def test_no_protected_entity_falls_back_to_length_ratio(self):
        source_protected = "Bu gercekten cok tuhaf bir durumdu herkes sasirmisti orada"  # no entities
        original = "Weird."  # suspiciously short vs. source
        chunked = "This was a really strange situation, everyone was surprised there."
        self.assertTrue(_prefer_chunked(original, chunked, source_protected, self.glossary_map))

    def test_no_signal_at_all_keeps_original(self):
        source_protected = "Bu gercekten cok tuhaf bir durumdu herkes sasirmisti orada"
        original = "This was a really strange situation."
        chunked = "Everyone was surprised by this strange thing."
        self.assertFalse(_prefer_chunked(original, chunked, source_protected, self.glossary_map))


class FindOrphanSpansTests(unittest.TestCase):
    """IMPROVEMENT_PLAN.md 3.2 / CLAUDE.md's "Known, not fixed" note: a
    single-word span isolated by a real acoustic gap gets zero context.
    _find_orphan_spans() only ever flags a span gap-adjacent on EXACTLY
    ONE side -- the gap side is where the real continuation is (see its
    own docstring for the "Her"/"şey olur, her şey biter" example)."""

    def test_gap_after_orphan_uses_the_next_span_as_context(self):
        # The neighbor cue is deliberately multi-word (not itself a
        # candidate orphan) so this test isolates the "Her" span's own
        # classification, not an accidental second flag on its neighbor.
        cues = [cue(0, 0.0, 1.0, "Her"),
                cue(1, 5.0, 6.0, "seyler oluyor", boundary=BoundaryReason.REAL_ACOUSTIC_GAP)]
        self.assertEqual(_find_orphan_spans(cues, [[0], [1]]), {0: (1, False)})

    def test_gap_before_orphan_uses_the_previous_span_as_context(self):
        cues = [cue(0, 0.0, 1.0, "context here"),
                cue(1, 5.0, 6.0, "Her", boundary=BoundaryReason.REAL_ACOUSTIC_GAP)]
        self.assertEqual(_find_orphan_spans(cues, [[0], [1]]), {1: (0, True)})

    def test_gap_on_both_sides_is_ambiguous_and_skipped(self):
        # Both neighbors are multi-word (never candidate orphans
        # themselves), isolating "Her" as the only span under test.
        cues = [cue(0, 0.0, 1.0, "context one"),
                cue(1, 5.0, 6.0, "Her", boundary=BoundaryReason.REAL_ACOUSTIC_GAP),
                cue(2, 10.0, 11.0, "context two", boundary=BoundaryReason.REAL_ACOUSTIC_GAP)]
        self.assertEqual(_find_orphan_spans(cues, [[0], [1], [2]]), {})

    def test_no_gap_on_either_side_is_not_an_orphan(self):
        cues = [cue(0, 0.0, 1.0, "a"), cue(1, 1.2, 2.0, "Her"), cue(2, 2.2, 3.0, "b")]
        self.assertEqual(_find_orphan_spans(cues, [[0], [1], [2]]), {})

    def test_multi_word_span_is_never_flagged_even_if_gap_adjacent(self):
        cues = [cue(0, 0.0, 1.0, "Her seyler"),
                cue(1, 5.0, 6.0, "devam ediyor", boundary=BoundaryReason.REAL_ACOUSTIC_GAP)]
        self.assertEqual(_find_orphan_spans(cues, [[0], [1]]), {})

    def test_gap_before_the_very_first_span_has_no_previous_neighbor(self):
        cues = [cue(0, 0.0, 1.0, "Her", boundary=BoundaryReason.REAL_ACOUSTIC_GAP)]
        self.assertEqual(_find_orphan_spans(cues, [[0]]), {})


class StripAnchorAffixTests(unittest.TestCase):
    def test_context_before_strips_the_leading_anchor_words(self):
        self.assertEqual(
            _strip_anchor_affix("cats are cute dogs too", "cats are cute", context_before=True),
            "dogs too")

    def test_context_after_strips_the_trailing_anchor_words(self):
        self.assertEqual(
            _strip_anchor_affix("dogs too cats are cute", "cats are cute", context_before=False),
            "dogs too")

    def test_case_and_punctuation_insensitive_match(self):
        self.assertEqual(
            _strip_anchor_affix("Dogs cats are cute.", "cats are cute", context_before=False),
            "Dogs")

    def test_mismatched_affix_returns_none(self):
        self.assertIsNone(
            _strip_anchor_affix("the dogs are loud", "cats are cute", context_before=False))

    def test_no_residual_beyond_the_anchor_returns_none(self):
        # Padded translation is the SAME length as the anchor -- nothing
        # distinguishable was actually added, e.g. NLLB just re-cased a
        # word instead of translating a genuinely new one.
        self.assertIsNone(
            _strip_anchor_affix("cats are cute", "cats are cute", context_before=False))

    def test_empty_anchor_translation_returns_none(self):
        self.assertIsNone(_strip_anchor_affix("dogs cats", "", context_before=False))


class PadOrphanContextIntegrationTests(unittest.TestCase):
    """translate_spans() end-to-end: an orphan word translated alone
    ("Dogs" -> "out", standing in for the real "Her" -> "out" defect)
    gets replaced by a context-grounded candidate extracted from a small
    probe translation, without touching the neighbor span's own result."""

    TRANSLATIONS = {
        "Dogs": "out",
        "Cats are cute.": "cats are cute.",
        "Dogs Cats are cute.": "dogs cats are cute.",
    }

    def _fake_translate_batch(self, model, tok, bos, sentences, device, config,
                              batch_size=12, on_progress=None):
        return [self.TRANSLATIONS[s] for s in sentences]

    def test_orphan_gets_replaced_with_grounded_translation(self):
        cues = [cue(0, 0.0, 1.0, "Dogs"),
                cue(1, 5.0, 6.0, "Cats are cute.", boundary=BoundaryReason.REAL_ACOUSTIC_GAP)]
        spans = build_context_spans(cues)
        self.assertEqual(spans, [[0], [1]])  # sanity: real gap forces the split
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch", side_effect=self._fake_translate_batch):
            result = translate_spans(cues, spans, "tr")
        self.assertEqual(result, ["dogs", "cats are cute."])

    def test_neighbor_spans_own_translation_is_never_altered(self):
        cues = [cue(0, 0.0, 1.0, "Dogs"),
                cue(1, 5.0, 6.0, "Cats are cute.", boundary=BoundaryReason.REAL_ACOUSTIC_GAP)]
        spans = build_context_spans(cues)
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch", side_effect=self._fake_translate_batch):
            result = translate_spans(cues, spans, "tr")
        self.assertEqual(result[1], "cats are cute.")

    def test_no_orphans_costs_zero_extra_translate_batch_calls(self):
        """The common case (no isolated single-word span next to a real
        gap) must add no extra model calls -- this is a rare-case pass,
        not overhead on every job."""
        cues = [cue(0, 0.0, 1.0, "Eda neredesin")]
        spans = build_context_spans(cues)
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch", return_value=["Eda where are you"]) as mock_batch:
            translate_spans(cues, spans, "tr")
        mock_batch.assert_called_once()

    def test_failed_extraction_falls_back_to_the_original_translation(self):
        """A padded probe whose translation doesn't cleanly contain the
        anchor's own wording must never silently invent a replacement --
        the orphan keeps its original (today's) translation."""
        cues = [cue(0, 0.0, 1.0, "Dogs"),
                cue(1, 5.0, 6.0, "Cats are cute.", boundary=BoundaryReason.REAL_ACOUSTIC_GAP)]
        spans = build_context_spans(cues)
        translations = dict(self.TRANSLATIONS)
        translations["Dogs Cats are cute."] = "completely different wording"

        def fake_batch(model, tok, bos, sentences, device, config, batch_size=12, on_progress=None):
            return [translations[s] for s in sentences]

        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch", side_effect=fake_batch):
            result = translate_spans(cues, spans, "tr")
        self.assertEqual(result[0], "out")   # unchanged from the ungrounded translation

    def test_disabled_via_env_var_skips_the_padding_pass_entirely(self):
        import os
        cues = [cue(0, 0.0, 1.0, "Dogs"),
                cue(1, 5.0, 6.0, "Cats are cute.", boundary=BoundaryReason.REAL_ACOUSTIC_GAP)]
        spans = build_context_spans(cues)
        with patch.dict(os.environ, {"SUBTITLE_AI_ORPHAN_CONTEXT_PADDING": "off"}), \
             patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch", side_effect=self._fake_translate_batch) as mock_batch:
            result = translate_spans(cues, spans, "tr")
        self.assertEqual(result[0], "out")   # padding pass never ran
        mock_batch.assert_called_once()      # only the main batch, no probe


class SrtCuesWithoutBoundaryProvenanceTests(unittest.TestCase):
    """Regression for a real production failure (2026-09-24, job
    6813fc33): srt_translation.py passes ValidatedCue objects (number/
    start/end/text only -- no boundary_before) into the SAME
    translate_spans() the video pipeline uses. _find_orphan_spans()
    accessed cue.boundary_before directly, so any SRT with a single-word
    cue (extremely common: "Hey.", "Eda!") crashed AFTER the entire
    translation had already finished. No existing test caught it because
    test_srt_translation.py mocks translate_spans() entirely."""

    class _SrtCue:  # exactly ValidatedCue's shape
        def __init__(self, number, start, end, text):
            self.number, self.start, self.end, self.text = number, start, end, text

    def test_find_orphan_spans_tolerates_cues_with_no_boundary_before(self):
        cues = [self._SrtCue(1, 0.0, 1.0, "Hey"), self._SrtCue(2, 1.0, 2.0, "How are you")]
        self.assertEqual(_find_orphan_spans(cues, [[0], [1]]), {})

    def test_translate_spans_completes_for_srt_shaped_cues_with_a_single_word_cue(self):
        cues = [self._SrtCue(1, 0.0, 1.0, "Hey"), self._SrtCue(2, 1.0, 2.0, "How are you")]
        fake = {"Hey": "Hey", "How are you": "How are you"}
        with patch("translate.load_model", return_value=(object(), object(), 0)), \
             patch("translate.translate_batch",
                   side_effect=lambda m, t, b, sents, *a, **k: [fake[s] for s in sents]):
            result = translate_spans(cues, [[0], [1]], "tr")
        self.assertEqual(result, ["Hey", "How are you"])


class OrphanContextPaddingEnabledTests(unittest.TestCase):
    def test_on_by_default(self):
        import os
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUBTITLE_AI_ORPHAN_CONTEXT_PADDING", None)
            self.assertTrue(orphan_context_padding_enabled())

    def test_off_values(self):
        import os
        for v in ("off", "OFF", "0", "false", "No"):
            with patch.dict(os.environ, {"SUBTITLE_AI_ORPHAN_CONTEXT_PADDING": v}):
                self.assertFalse(orphan_context_padding_enabled(), v)

    def test_anything_else_stays_enabled(self):
        import os
        with patch.dict(os.environ, {"SUBTITLE_AI_ORPHAN_CONTEXT_PADDING": "on"}):
            self.assertTrue(orphan_context_padding_enabled())


if __name__ == "__main__":
    unittest.main()
