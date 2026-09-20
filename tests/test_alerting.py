"""alerting.py -- the optional job-failure webhook (production-readiness
gap: a job failure was previously invisible until someone opened the
UI). See alerting.py's module docstring."""

import unittest
from unittest.mock import patch

import httpx

from alerting import notify_job_failed


class NotifyJobFailedTests(unittest.TestCase):
    def test_disabled_by_default_makes_no_request(self):
        with patch.dict("os.environ", {}, clear=True), \
             patch("alerting.httpx.post") as mock_post:
            notify_job_failed({"id": "abc123", "error": "boom"})
        mock_post.assert_not_called()

    def test_posts_job_fields_when_configured(self):
        job = {"id": "abc123", "job_type": "srt_translation", "video_path": "Show/S01E01.mkv",
              "source_srt_path": "Show/S01E01.tr.srt", "error": "boom",
              "error_category": "PIPELINE_ERROR"}
        with patch.dict("os.environ", {"FAILURE_WEBHOOK_URL": "http://example.invalid/hook"}), \
             patch("alerting.httpx.post") as mock_post:
            notify_job_failed(job)
        mock_post.assert_called_once()
        url, kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
        self.assertEqual(url, "http://example.invalid/hook")
        self.assertEqual(kwargs["json"]["job_id"], "abc123")
        self.assertEqual(kwargs["json"]["error"], "boom")

    def test_webhook_failure_is_swallowed_not_raised(self):
        with patch.dict("os.environ", {"FAILURE_WEBHOOK_URL": "http://example.invalid/hook"}), \
             patch("alerting.httpx.post", side_effect=httpx.ConnectError("down")):
            notify_job_failed({"id": "abc123"})  # must not raise


if __name__ == "__main__":
    unittest.main()
