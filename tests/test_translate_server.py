"""translate_server.py: the remote translate-server's HTTP surface.
load_model()/translate_batch() are mocked throughout -- this module is a
thin dispatch layer over translate.py's already-tested functions, not a
place to re-test NLLB itself."""

import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import translate_server
from translate import NLLB_LANG


def _reset_state():
    # Fresh state per test -- the module-level _state dict would
    # otherwise leak a "loaded" model across tests.
    translate_server._state.update(
        model=None, bos=None, tokenizers={}, last_used=None, active_requests=0,
        config=translate_server.TranslationConfig())


class TranslateServerTests(unittest.TestCase):
    def setUp(self):
        _reset_state()

    def test_health_reports_device_and_loaded_default_language(self):
        with patch("translate_server.load_model", return_value=(object(), object(), 0)):
            with TestClient(translate_server.app) as client:
                resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn(NLLB_LANG["tr"], body["loaded_languages"])

    def test_translate_endpoint_returns_translations(self):
        with patch("translate_server.load_model", return_value=(object(), object(), 0)), \
             patch("translate_server.translate_batch", return_value=["Hello", "World"]) as mock_batch:
            with TestClient(translate_server.app) as client:
                resp = client.post("/translate", json={"sentences": ["Merhaba", "Dunya"], "src_lang": "tr"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"translations": ["Hello", "World"]})
        mock_batch.assert_called_once()
        self.assertEqual(mock_batch.call_args[0][3], ["Merhaba", "Dunya"])

    def test_default_language_is_not_reloaded_on_first_matching_request(self):
        with patch("translate_server.load_model", return_value=(object(), object(), 0)) as mock_load, \
             patch("translate_server.translate_batch", return_value=["Hello"]):
            with TestClient(translate_server.app) as client:
                # load_model already ran once at startup for the default
                # language ("tr") -- a request for the SAME language must
                # reuse it, not load a second time.
                client.post("/translate", json={"sentences": ["Merhaba"], "src_lang": "tr"})
        mock_load.assert_called_once()

    def test_new_language_loads_only_a_tokenizer_never_a_second_model(self):
        with patch("translate_server.load_model", return_value=(object(), object(), 0)) as mock_load, \
             patch("translate_server.load_tokenizer", return_value=object()) as mock_tok, \
             patch("translate_server.translate_batch", return_value=["Hello"]):
            with TestClient(translate_server.app) as client:
                client.post("/translate", json={"sentences": ["Konnichiwa"], "src_lang": "ja"})
                client.post("/translate", json={"sentences": ["Bonjour"], "src_lang": "fr"})
                client.post("/translate", json={"sentences": ["Konnichiwa"], "src_lang": "ja"})
        # One model load, at startup for the default ("tr"); "ja" and
        # "fr" each cost a single tokenizer, and a repeat costs nothing.
        mock_load.assert_called_once()
        self.assertEqual(mock_tok.call_count, 2)

    def test_all_languages_share_the_same_model_instance(self):
        seen = []
        model = object()
        with patch("translate_server.load_model", return_value=(model, object(), 0)), \
             patch("translate_server.load_tokenizer", return_value=object()), \
             patch("translate_server.translate_batch",
                   side_effect=lambda m, *a, **k: seen.append(m) or ["x"]):
            with TestClient(translate_server.app) as client:
                for lang in ("tr", "ja", "fr"):
                    client.post("/translate", json={"sentences": ["s"], "src_lang": lang})
        self.assertEqual(seen, [model, model, model])

    def test_unsupported_src_lang_is_422_not_500(self):
        with patch("translate_server.load_model", return_value=(object(), object(), 0)):
            with TestClient(translate_server.app) as client:
                resp = client.post("/translate", json={"sentences": ["x"], "src_lang": "zz"})
        self.assertEqual(resp.status_code, 422)

    def test_health_reports_model_loaded(self):
        with patch("translate_server.load_model", return_value=(object(), object(), 0)):
            with TestClient(translate_server.app) as client:
                self.assertTrue(client.get("/health").json()["model_loaded"])

    def test_active_requests_returns_to_zero_even_when_inference_raises(self):
        with patch("translate_server.load_model", return_value=(object(), object(), 0)), \
             patch("translate_server.translate_batch", side_effect=RuntimeError("boom")):
            with TestClient(translate_server.app, raise_server_exceptions=False) as client:
                resp = client.post("/translate", json={"sentences": ["x"], "src_lang": "tr"})
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(translate_server._state["active_requests"], 0)


def _load(code):
    with translate_server._infer_lock:
        return translate_server._load_for(code)


class IdleUnloadTests(unittest.TestCase):
    """Real motivation (2026-09-20): this GPU is shared with Jellyfin/Plex's
    own hardware transcoding on the same host -- a model kept loaded
    forever would permanently eat into their VRAM headroom even during
    long stretches with no translation job running."""

    def setUp(self):
        _reset_state()

    def test_nothing_loaded_does_not_unload(self):
        self.assertFalse(translate_server._unload_if_idle())

    def test_finishing_a_request_resets_the_idle_clock(self):
        # Loaded at t=1000, a request COMPLETES at t=1200 -- the idle
        # check at t=1200+IDLE_UNLOAD_SECONDS-1 must count from the latest
        # completion, not the original load, or an actively-reused model
        # would get unloaded out from under a still-busy deployment.
        with patch("translate_server.load_model", return_value=(object(), object(), 0)), \
             patch("translate_server.translate_batch", return_value=["x"]):
            with patch("translate_server.time.monotonic", return_value=1000.0):
                _load("tur_Latn")
            with TestClient(translate_server.app) as client:
                with patch("translate_server.time.monotonic", return_value=1200.0):
                    client.post("/translate", json={"sentences": ["x"], "src_lang": "tr"})
                with patch("translate_server.time.monotonic",
                          return_value=1200.0 + translate_server.IDLE_UNLOAD_SECONDS - 1):
                    unloaded = translate_server._unload_if_idle()
        self.assertFalse(unloaded)

    def test_unloads_after_timeout_elapses(self):
        with patch("translate_server.load_model", return_value=(object(), object(), 0)):
            with patch("translate_server.time.monotonic", return_value=1000.0):
                _load("tur_Latn")
            with patch("translate_server.time.monotonic",
                      return_value=1000.0 + translate_server.IDLE_UNLOAD_SECONDS + 1):
                unloaded = translate_server._unload_if_idle()
        self.assertTrue(unloaded)
        self.assertIsNone(translate_server._state["model"])
        self.assertEqual(translate_server._state["tokenizers"], {})

    def test_does_not_unload_before_timeout_elapses(self):
        with patch("translate_server.load_model", return_value=(object(), object(), 0)):
            with patch("translate_server.time.monotonic", return_value=1000.0):
                _load("tur_Latn")
            with patch("translate_server.time.monotonic",
                      return_value=1000.0 + translate_server.IDLE_UNLOAD_SECONDS - 1):
                unloaded = translate_server._unload_if_idle()
        self.assertFalse(unloaded)
        self.assertIn("tur_Latn", translate_server._state["tokenizers"])

    def test_reloads_transparently_on_next_request_after_idle_unload(self):
        with patch("translate_server.load_model", return_value=(object(), object(), 0)) as mock_load, \
             patch("translate_server.translate_batch", return_value=["Hello"]):
            with patch("translate_server.time.monotonic", return_value=1000.0):
                _load("tur_Latn")
            with patch("translate_server.time.monotonic",
                      return_value=1000.0 + translate_server.IDLE_UNLOAD_SECONDS + 1):
                translate_server._unload_if_idle()
            self.assertEqual(mock_load.call_count, 1)
            with TestClient(translate_server.app) as client:
                # App startup itself calls _load_for("tur_Latn") again since
                # the cache is now empty -- then the request reuses it.
                resp = client.post("/translate", json={"sentences": ["Merhaba"], "src_lang": "tr"})
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(mock_load.call_count, 2)

    def test_does_not_unload_while_a_request_is_active(self):
        with patch("translate_server.load_model", return_value=(object(), object(), 0)):
            with patch("translate_server.time.monotonic", return_value=1000.0):
                _load("tur_Latn")
            translate_server._state["active_requests"] = 1
            with patch("translate_server.time.monotonic",
                      return_value=1000.0 + translate_server.IDLE_UNLOAD_SECONDS * 10):
                unloaded = translate_server._unload_if_idle()
        self.assertFalse(unloaded)
        self.assertIsNotNone(translate_server._state["model"])

    def test_unloads_after_the_active_request_finishes_and_idle_window_passes(self):
        with patch("translate_server.load_model", return_value=(object(), object(), 0)), \
             patch("translate_server.translate_batch", return_value=["x"]):
            with patch("translate_server.time.monotonic", return_value=1000.0):
                _load("tur_Latn")
            with TestClient(translate_server.app) as client:
                # A request starts and finishes at t=5000 -- last_used must
                # be refreshed at COMPLETION, not just at start.
                with patch("translate_server.time.monotonic", return_value=5000.0):
                    client.post("/translate", json={"sentences": ["x"], "src_lang": "tr"})
                self.assertEqual(translate_server._state["last_used"], 5000.0)
                with patch("translate_server.time.monotonic",
                          return_value=5000.0 + translate_server.IDLE_UNLOAD_SECONDS - 1):
                    self.assertFalse(translate_server._unload_if_idle())
                with patch("translate_server.time.monotonic",
                          return_value=5000.0 + translate_server.IDLE_UNLOAD_SECONDS + 1):
                    self.assertTrue(translate_server._unload_if_idle())
                self.assertIsNone(translate_server._state["model"])

    def test_slow_request_past_idle_timeout_is_not_evicted_and_causes_no_second_load(self):
        """A request that runs longer than IDLE_UNLOAD_SECONDS must keep
        the model resident: the idle check runs mid-request and must
        refuse, and the next request must reuse the same model."""
        model = object()
        started, release = threading.Event(), threading.Event()
        evicted_mid_request = []

        def slow_batch(m, *args, **kwargs):
            started.set()
            release.wait(timeout=5)
            return ["x"]

        with patch("translate_server.load_model", return_value=(model, object(), 0)) as mock_load, \
             patch("translate_server.translate_batch", side_effect=slow_batch):
            with TestClient(translate_server.app) as client:
                results = []
                t = threading.Thread(target=lambda: results.append(
                    client.post("/translate", json={"sentences": ["x"], "src_lang": "tr"})))
                t.start()
                self.assertTrue(started.wait(timeout=5))
                # Far past the idle window while the request is in flight.
                with patch("translate_server.time.monotonic",
                          return_value=time.monotonic() + translate_server.IDLE_UNLOAD_SECONDS * 10):
                    evicted_mid_request.append(translate_server._unload_if_idle())
                release.set()
                t.join(timeout=5)
                client.post("/translate", json={"sentences": ["x"], "src_lang": "tr"})
        self.assertEqual(evicted_mid_request, [False])
        self.assertEqual(results[0].status_code, 200)
        mock_load.assert_called_once()

    def test_concurrent_requests_never_overlap_inference(self):
        in_flight, max_in_flight = [0], [0]
        guard = threading.Lock()

        def tracked_batch(m, *args, **kwargs):
            with guard:
                in_flight[0] += 1
                max_in_flight[0] = max(max_in_flight[0], in_flight[0])
            time.sleep(0.05)
            with guard:
                in_flight[0] -= 1
            return ["x"]

        with patch("translate_server.load_model", return_value=(object(), object(), 0)), \
             patch("translate_server.load_tokenizer", return_value=object()), \
             patch("translate_server.translate_batch", side_effect=tracked_batch):
            with TestClient(translate_server.app) as client:
                threads = [threading.Thread(
                    target=lambda lang=lang: client.post(
                        "/translate", json={"sentences": ["x"], "src_lang": lang}))
                    for lang in ("tr", "ja", "tr", "fr")]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=10)
        self.assertEqual(max_in_flight[0], 1)
        self.assertEqual(translate_server._state["active_requests"], 0)


class IdleUnloadEnvParsingTests(unittest.TestCase):
    """compose.translate-server.yml forwards
    TRANSLATE_SERVER_IDLE_UNLOAD_SECONDS as `${...:-}`, so an unset .env
    entry reaches the container as "" -- a bare float("") at import time
    would have crashed the server on startup. Same class of defect as
    gpu.py's SUBTITLE_AI_VRAM_MARGIN_GB (see test_vram_preflight.py)."""

    def _parse(self, value):
        import os
        with patch.dict(os.environ, {"TRANSLATE_SERVER_IDLE_UNLOAD_SECONDS": value}):
            return translate_server._idle_unload_seconds_from_env()

    def test_blank_falls_back_to_default(self):
        self.assertEqual(self._parse(""), 120.0)

    def test_whitespace_only_falls_back_to_default(self):
        self.assertEqual(self._parse("  "), 120.0)

    def test_valid_value_is_used(self):
        self.assertEqual(self._parse("300"), 300.0)

    def test_zero_is_valid_and_means_unload_at_the_next_idle_check(self):
        self.assertEqual(self._parse("0"), 0.0)

    def test_surrounding_whitespace_is_tolerated(self):
        self.assertEqual(self._parse(" 45 "), 45.0)

    def test_garbage_falls_back_to_default_instead_of_raising(self):
        self.assertEqual(self._parse("soon"), 120.0)

    def test_negative_falls_back_to_default(self):
        self.assertEqual(self._parse("-5"), 120.0)

    def test_unset_falls_back_to_default(self):
        import os
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TRANSLATE_SERVER_IDLE_UNLOAD_SECONDS", None)
            self.assertEqual(translate_server._idle_unload_seconds_from_env(), 120.0)

    def test_module_import_survives_a_blank_value(self):
        # The actual production failure mode: importing translate_server.
        import importlib
        import os
        with patch.dict(os.environ, {"TRANSLATE_SERVER_IDLE_UNLOAD_SECONDS": ""}):
            reloaded = importlib.reload(translate_server)
            self.assertEqual(reloaded.IDLE_UNLOAD_SECONDS, 120.0)
        importlib.reload(translate_server)  # restore for any tests running after this one


if __name__ == "__main__":
    unittest.main()
