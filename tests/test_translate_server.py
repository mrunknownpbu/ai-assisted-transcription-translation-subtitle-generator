"""translate_server.py: the remote translate-server's HTTP surface.
load_model()/translate_batch() are mocked throughout -- this module is a
thin dispatch layer over translate.py's already-tested functions, not a
place to re-test NLLB itself."""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import translate_server
from translate import NLLB_LANG


class TranslateServerTests(unittest.TestCase):
    def setUp(self):
        # Fresh cache per test -- the module-level _state dict would
        # otherwise leak a "loaded" language across tests.
        translate_server._state["models"] = {}

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

    def test_new_language_is_loaded_on_demand(self):
        with patch("translate_server.load_model", return_value=(object(), object(), 0)) as mock_load, \
             patch("translate_server.translate_batch", return_value=["Hello"]):
            with TestClient(translate_server.app) as client:
                client.post("/translate", json={"sentences": ["Konnichiwa"], "src_lang": "ja"})
        # Once for the default ("tr") at startup, once more for "ja".
        self.assertEqual(mock_load.call_count, 2)


if __name__ == "__main__":
    unittest.main()
