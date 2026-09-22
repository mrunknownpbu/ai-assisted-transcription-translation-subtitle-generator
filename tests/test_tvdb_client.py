"""TheTVDB API v4 client: auth, token caching/refresh, rate-limit/timeout
handling, disk caching, and the "no key configured" fallback path that
keeps TVDB from ever being a single point of failure.

All HTTP is mocked -- no real network calls, and no real API key is
configured in this environment anyway.
"""

import importlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

import tvdb_client


def _response(status=200, json_body=None):
    resp = Mock()
    resp.status_code = status
    resp.json.return_value = json_body or {}
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status}", request=Mock(), response=resp)
    else:
        resp.raise_for_status.side_effect = None
    return resp


class TvdbClientTestCase(unittest.TestCase):
    def setUp(self):
        # Each test gets a clean module state: no leaked token cache, no
        # leaked API key from a previous test, an isolated disk cache dir.
        importlib.reload(tvdb_client)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tvdb_client.CACHE_DIR = Path(self.tmp.name)

    def with_key(self, key="test-key"):
        tvdb_client.API_KEY = key
        return key


class NoCredentialsTests(TvdbClientTestCase):
    def test_configured_false_without_api_key(self):
        tvdb_client.API_KEY = None
        self.assertFalse(tvdb_client.configured())

    def test_series_returns_none_without_api_key(self):
        tvdb_client.API_KEY = None
        self.assertIsNone(tvdb_client.series(383383))

    def test_episode_returns_none_without_api_key(self):
        tvdb_client.API_KEY = None
        self.assertIsNone(tvdb_client.episode(383383, 1, 1))

    def test_no_network_call_is_attempted_without_a_key(self):
        tvdb_client.API_KEY = None
        with patch("tvdb_client.httpx.post") as post, patch("tvdb_client.httpx.get") as get:
            tvdb_client.series(383383)
            post.assert_not_called()
            get.assert_not_called()


class AuthTests(TvdbClientTestCase):
    def test_login_posts_api_key_and_returns_token(self):
        self.with_key("k1")
        with patch("tvdb_client.httpx.post", return_value=_response(200, {"data": {"token": "tok1"}})) as post:
            token = tvdb_client._token()
        self.assertEqual(token, "tok1")
        self.assertEqual(post.call_args.kwargs["json"], {"apikey": "k1"})

    def test_pin_included_when_configured(self):
        self.with_key("k1")
        tvdb_client.API_PIN = "1234"
        with patch("tvdb_client.httpx.post", return_value=_response(200, {"data": {"token": "tok1"}})) as post:
            tvdb_client._token()
        self.assertEqual(post.call_args.kwargs["json"], {"apikey": "k1", "pin": "1234"})

    def test_token_is_cached_across_calls(self):
        self.with_key()
        with patch("tvdb_client.httpx.post", return_value=_response(200, {"data": {"token": "tok1"}})) as post, \
             patch("tvdb_client.httpx.get", return_value=_response(200, {"data": {"id": 383383}})):
            tvdb_client.series(383383)
            tvdb_client._cache_path("series", "383383").unlink()  # bypass disk cache for this check
            tvdb_client.series(383383)
        self.assertEqual(post.call_count, 1, "a cached token must not trigger a second /login")

    def test_login_failure_raises_internal_unavailable(self):
        self.with_key()
        with patch("tvdb_client.httpx.post", side_effect=httpx.ConnectError("down")):
            with self.assertRaises(tvdb_client.TvdbUnavailable):
                tvdb_client._token()

    def test_public_functions_degrade_to_none_on_login_failure(self):
        self.with_key()
        with patch("tvdb_client.httpx.post", side_effect=httpx.ConnectError("down")):
            self.assertIsNone(tvdb_client.series(383383))  # never raises out to the caller


class RequestHandlingTests(TvdbClientTestCase):
    def test_401_triggers_one_relogin_retry(self):
        self.with_key()
        with patch("tvdb_client.httpx.post", return_value=_response(200, {"data": {"token": "tok1"}})), \
             patch("tvdb_client.httpx.get",
                   side_effect=[_response(401), _response(200, {"data": {"id": 383383}})]) as get:
            data = tvdb_client._get("/series/383383")
        self.assertEqual(data, {"id": 383383})
        self.assertEqual(get.call_count, 2)

    def test_401_does_not_loop_forever(self):
        self.with_key()
        with patch("tvdb_client.httpx.post", return_value=_response(200, {"data": {"token": "tok1"}})), \
             patch("tvdb_client.httpx.get", return_value=_response(401)) as get:
            data = tvdb_client._get("/series/383383")
        self.assertIsNone(data)
        self.assertEqual(get.call_count, 2)  # one retry, then give up -- never an infinite loop

    def test_429_returns_none_without_retry_or_blocking(self):
        self.with_key()
        with patch("tvdb_client.httpx.post", return_value=_response(200, {"data": {"token": "tok1"}})), \
             patch("tvdb_client.httpx.get", return_value=_response(429)) as get:
            data = tvdb_client._get("/series/383383")
        self.assertIsNone(data)
        self.assertEqual(get.call_count, 1, "a rate limit must not trigger a retry loop")

    def test_timeout_degrades_to_none(self):
        self.with_key()
        with patch("tvdb_client.httpx.post", return_value=_response(200, {"data": {"token": "tok1"}})), \
             patch("tvdb_client.httpx.get", side_effect=httpx.TimeoutException("slow")):
            self.assertIsNone(tvdb_client._get("/series/383383"))

    def test_requests_use_a_bounded_timeout(self):
        self.with_key()
        with patch("tvdb_client.httpx.post", return_value=_response(200, {"data": {"token": "tok1"}})), \
             patch("tvdb_client.httpx.get", return_value=_response(200, {"data": {}})) as get:
            tvdb_client._get("/series/383383")
        self.assertEqual(get.call_args.kwargs["timeout"], tvdb_client.REQUEST_TIMEOUT)


class CachingTests(TvdbClientTestCase):
    def test_series_result_is_cached_to_disk(self):
        self.with_key()
        with patch("tvdb_client.httpx.post", return_value=_response(200, {"data": {"token": "tok1"}})), \
             patch("tvdb_client.httpx.get", return_value=_response(200, {"data": {"name": "Sen Çal Kapımı"}})) as get:
            tvdb_client.series(383383)
            tvdb_client.series(383383)  # second call within TTL must hit the disk cache, not the network
        self.assertEqual(get.call_count, 1)
        cached = json.loads(tvdb_client._cache_path("series", "383383").read_text(encoding="utf-8"))
        self.assertEqual(cached["name"], "Sen Çal Kapımı")

    def test_stale_cache_entry_is_refetched(self):
        self.with_key()
        path = tvdb_client._cache_path("series", "383383")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"name": "stale"}), encoding="utf-8")
        stale_time = time.time() - tvdb_client.CACHE_TTL - 10
        os.utime(path, (stale_time, stale_time))
        with patch("tvdb_client.httpx.post", return_value=_response(200, {"data": {"token": "tok1"}})), \
             patch("tvdb_client.httpx.get", return_value=_response(200, {"data": {"name": "fresh"}})):
            data = tvdb_client.series(383383)
        self.assertEqual(data["name"], "fresh")

    def test_corrupt_cache_file_is_refetched_not_fatal(self):
        self.with_key()
        path = tvdb_client._cache_path("series", "383383")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")
        with patch("tvdb_client.httpx.post", return_value=_response(200, {"data": {"token": "tok1"}})), \
             patch("tvdb_client.httpx.get", return_value=_response(200, {"data": {"name": "recovered"}})):
            data = tvdb_client.series(383383)
        self.assertEqual(data["name"], "recovered")


class EpisodeResolutionTests(TvdbClientTestCase):
    FAKE_EPISODES = {
        "episodes": [
            {"seasonNumber": 1, "number": 1, "name": "Pilot", "id": 111},
            {"seasonNumber": 1, "number": 2, "name": "Episode Two", "id": 112},
            {"seasonNumber": 0, "number": 1, "name": "A Special", "id": 999},  # specials -- must not be matched
        ]
    }

    def test_matches_aired_order_season_and_episode(self):
        self.with_key()
        with patch("tvdb_client.httpx.post", return_value=_response(200, {"data": {"token": "tok1"}})), \
             patch("tvdb_client.httpx.get", return_value=_response(200, {"data": self.FAKE_EPISODES})):
            ep = tvdb_client.episode(383383, 1, 1)
        self.assertEqual(ep["name"], "Pilot")

    def test_specials_are_not_matched_as_a_numbered_episode(self):
        self.with_key()
        with patch("tvdb_client.httpx.post", return_value=_response(200, {"data": {"token": "tok1"}})), \
             patch("tvdb_client.httpx.get", return_value=_response(200, {"data": self.FAKE_EPISODES})):
            ep = tvdb_client.episode(383383, 0, 99)
        self.assertIsNone(ep)

    def test_no_confident_match_returns_none_not_invented_metadata(self):
        self.with_key()
        with patch("tvdb_client.httpx.post", return_value=_response(200, {"data": {"token": "tok1"}})), \
             patch("tvdb_client.httpx.get", return_value=_response(200, {"data": self.FAKE_EPISODES})):
            ep = tvdb_client.episode(383383, 5, 5)
        self.assertIsNone(ep)


if __name__ == "__main__":
    unittest.main()
