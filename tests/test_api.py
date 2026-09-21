import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import api
import audio_streams
from media import MediaError

SERIES_GLOSSARY_YAML = """
tvdb_id: 111
title: "Test Series"
entities:
  - canonical: Eda
    type: character
    protected: true
    aliases: []
"""


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        app = api.create_app(Path(self.tmp.name) / "jobs.db")
        self.client = TestClient(app)


class MediaRootApiTestCase(unittest.TestCase):
    """Tests that need a real on-disk media root, not just a job db --
    for /api/browse and /api/media."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "media"
        self.root.mkdir()
        (self.root / "Show").mkdir()
        (self.root / "Show" / "S01E01.mkv").write_bytes(b"not a real video, just needs to exist")
        (self.root / "Show" / "notes.txt").write_text("not a video")
        self.upload_dir = Path(self.tmp.name) / "srt_uploads"  # deliberately OUTSIDE self.root
        app = api.create_app(Path(self.tmp.name) / "jobs.db", str(self.root),
                             srt_upload_dir=str(self.upload_dir))
        self.client = TestClient(app)


class HealthTests(ApiTestCase):
    def test_health_ok(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_health_omits_heartbeat_when_no_worker_registered(self):
        r = self.client.get("/api/health")
        self.assertNotIn("worker_last_heartbeat_seconds_ago", r.json())

    def test_health_reports_registered_worker_heartbeat(self):
        import time
        import types
        api.register_worker(types.SimpleNamespace(last_heartbeat=time.time()))
        try:
            r = self.client.get("/api/health")
            self.assertIn("worker_last_heartbeat_seconds_ago", r.json())
            self.assertLess(r.json()["worker_last_heartbeat_seconds_ago"], 5.0)
        finally:
            api.register_worker(None)


class ApiKeyGuardTests(ApiTestCase):
    """Real gap this closes (production-readiness audit, 2026-09-21):
    every endpoint had zero access control. Opt-in only -- unset by
    default, matching the confirmed LAN-only deployment's current
    behavior exactly."""

    def test_unset_key_leaves_mutating_endpoints_open(self):
        with patch.dict("os.environ", {}, clear=True):
            job = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv", "source_lang": "tr"})
            r = self.client.post(f"/api/jobs/{job.json()['job']['id']}/cancel")
        self.assertEqual(r.status_code, 200)

    def test_configured_key_blocks_request_with_no_header(self):
        job = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv", "source_lang": "tr"})
        with patch.dict("os.environ", {"SUBTITLE_AI_API_KEY": "secret123"}):
            r = self.client.post(f"/api/jobs/{job.json()['job']['id']}/cancel")
        self.assertEqual(r.status_code, 401)

    def test_configured_key_blocks_request_with_wrong_header(self):
        job = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv", "source_lang": "tr"})
        with patch.dict("os.environ", {"SUBTITLE_AI_API_KEY": "secret123"}):
            r = self.client.post(f"/api/jobs/{job.json()['job']['id']}/cancel",
                                 headers={"X-API-Key": "wrong"})
        self.assertEqual(r.status_code, 401)

    def test_configured_key_allows_request_with_correct_header(self):
        job = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv", "source_lang": "tr"})
        with patch.dict("os.environ", {"SUBTITLE_AI_API_KEY": "secret123"}):
            r = self.client.post(f"/api/jobs/{job.json()['job']['id']}/cancel",
                                 headers={"X-API-Key": "secret123"})
        self.assertEqual(r.status_code, 200)

    def test_read_only_endpoints_stay_open_even_with_key_configured(self):
        with patch.dict("os.environ", {"SUBTITLE_AI_API_KEY": "secret123"}):
            r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)


class FailureWebhookWiringTests(ApiTestCase):
    """Real gap this closes (production-readiness audit, 2026-09-21): a
    job failure was previously invisible until someone opened the UI."""

    def test_job_reaching_failed_status_notifies(self):
        job = api.get_store().create("Show/S01E01.mkv", "tr")
        with patch("api.alerting.notify_job_failed") as mock_notify:
            api.get_store().finish(job["id"], "failed", error="boom",
                                   error_category="PIPELINE_ERROR")
        mock_notify.assert_called_once()
        self.assertEqual(mock_notify.call_args[0][0]["id"], job["id"])

    def test_job_reaching_completed_status_does_not_notify(self):
        job = api.get_store().create("Show/S01E01.mkv", "tr")
        with patch("api.alerting.notify_job_failed") as mock_notify:
            api.get_store().finish(job["id"], "completed")
        mock_notify.assert_not_called()


class CreateJobTests(ApiTestCase):
    def test_creates_queued_job(self):
        r = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv", "source_lang": "tr"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["job"]["status"], "queued")

    def test_rejects_path_traversal(self):
        r = self.client.post("/api/jobs", json={"video_path": "../../etc/passwd", "source_lang": "tr"})
        self.assertEqual(r.status_code, 400)

    def test_rejects_invalid_language_code(self):
        r = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv", "source_lang": "TR"})
        self.assertEqual(r.status_code, 422)

    def test_source_lang_defaults_to_auto_when_omitted(self):
        r = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["job"]["source_lang"], "auto")
        self.assertEqual(r.json()["job"]["source_language_mode"], "AUTO")

    def test_explicit_auto_is_accepted(self):
        r = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv", "source_lang": "auto"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["job"]["source_lang"], "auto")

    def test_unsupported_but_valid_shaped_language_code_rejected_with_400(self):
        # Matches POST /api/srt-translations' existing stricter check --
        # a well-formed-looking code that isn't a real NLLB_LANG key
        # must not reach the worker/pipeline unchecked.
        r = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv", "source_lang": "zz"})
        self.assertEqual(r.status_code, 400)

    def test_target_lang_defaults_to_english(self):
        r = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv"})
        self.assertEqual(r.json()["job"]["target_lang"], "en")

    def test_non_english_target_lang_rejected_with_422(self):
        r = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv", "target_lang": "fr"})
        self.assertEqual(r.status_code, 422)
        self.assertTrue(r.json()["detail"][0]["msg"].endswith(
            'target_lang must be "en"; multi-target translation is not supported'))
        self.assertEqual(self.client.get("/api/jobs").json()["total"], 0)

    def test_explicit_english_target_lang_still_accepted(self):
        r = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv", "target_lang": "en"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["job"]["target_lang"], "en")

    def test_audio_stream_index_defaults_to_auto(self):
        r = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv"})
        job = r.json()["job"]
        self.assertIsNone(job["requested_audio_stream"])
        self.assertEqual(job["stream_selection_mode"], "AUTO")

    def test_explicit_audio_stream_index_sets_manual_mode(self):
        r = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv", "audio_stream_index": 2})
        job = r.json()["job"]
        self.assertEqual(job["requested_audio_stream"], 2)
        self.assertEqual(job["stream_selection_mode"], "MANUAL")

    def test_negative_audio_stream_index_rejected(self):
        r = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv", "audio_stream_index": -1})
        self.assertEqual(r.status_code, 422)


class ListAndQueueTests(ApiTestCase):
    def test_list_and_queue_counts_reflect_created_jobs(self):
        self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv"})
        self.client.post("/api/jobs", json={"video_path": "Show/S01E02.mkv"})
        r = self.client.get("/api/jobs")
        self.assertEqual(r.json()["total"], 2)
        r = self.client.get("/api/queue")
        self.assertEqual(r.json()["ALL"], 2)
        self.assertEqual(r.json()["QUEUED"], 2)

    def test_status_filter(self):
        self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv"})
        r = self.client.get("/api/jobs", params={"status": "running"})
        self.assertEqual(r.json()["total"], 0)


class GetJobTests(ApiTestCase):
    def test_get_existing_job(self):
        created = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv"}).json()["job"]
        r = self.client.get(f"/api/jobs/{created['id']}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["id"], created["id"])

    def test_get_missing_job_404(self):
        r = self.client.get("/api/jobs/does-not-exist")
        self.assertEqual(r.status_code, 404)


class CancelTests(ApiTestCase):
    def test_cancel_queued_job(self):
        created = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv"}).json()["job"]
        r = self.client.post(f"/api/jobs/{created['id']}/cancel")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "cancelled")

    def test_cancel_missing_job_404(self):
        r = self.client.post("/api/jobs/does-not-exist/cancel")
        self.assertEqual(r.status_code, 404)


class RetryLanguageOverrideTests(ApiTestCase):
    def test_retry_without_override_keeps_auto(self):
        created = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv"}).json()["job"]
        self.client.post(f"/api/jobs/{created['id']}/cancel")
        r = self.client.post(f"/api/jobs/{created['id']}/retry", json={})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["job"]["source_lang"], "auto")

    def test_retry_with_manual_language_override(self):
        created = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv"}).json()["job"]
        self.client.post(f"/api/jobs/{created['id']}/cancel")
        r = self.client.post(f"/api/jobs/{created['id']}/retry", json={"source_lang": "tr"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["job"]["source_lang"], "tr")
        self.assertEqual(r.json()["job"]["source_language_mode"], "MANUAL")

    def test_retry_with_no_body_carries_stream_selection_forward(self):
        created = self.client.post(
            "/api/jobs", json={"video_path": "Show/S01E01.mkv", "audio_stream_index": 2}).json()["job"]
        self.client.post(f"/api/jobs/{created['id']}/cancel")
        r = self.client.post(f"/api/jobs/{created['id']}/retry")
        self.assertEqual(r.json()["job"]["requested_audio_stream"], 2)

    def test_retry_with_explicit_null_stream_forces_auto(self):
        created = self.client.post(
            "/api/jobs", json={"video_path": "Show/S01E01.mkv", "audio_stream_index": 2}).json()["job"]
        self.client.post(f"/api/jobs/{created['id']}/cancel")
        r = self.client.post(f"/api/jobs/{created['id']}/retry", json={"audio_stream_index": None})
        self.assertIsNone(r.json()["job"]["requested_audio_stream"])
        self.assertEqual(r.json()["job"]["stream_selection_mode"], "AUTO")


class RetryOverwriteInheritanceTests(ApiTestCase):
    """Real bug (2026-09-19): the GUI's one-click Retry button POSTs an
    empty body, which used to silently reset both overwrite flags to
    False regardless of the original job's values."""

    def test_empty_body_retry_inherits_true_overwrite_flags(self):
        created = self.client.post("/api/jobs", json={
            "video_path": "Show/S01E01.mkv", "overwrite_original": True,
            "overwrite_english": True}).json()["job"]
        self.client.post(f"/api/jobs/{created['id']}/cancel")
        r = self.client.post(f"/api/jobs/{created['id']}/retry", json={})
        self.assertTrue(r.json()["job"]["overwrite_original"])
        self.assertTrue(r.json()["job"]["overwrite_english"])

    def test_no_body_at_all_retry_inherits_true_overwrite_flags(self):
        # The GUI's actual call shape -- no json= argument, not even {}.
        created = self.client.post("/api/jobs", json={
            "video_path": "Show/S01E01.mkv", "overwrite_original": True,
            "overwrite_english": True}).json()["job"]
        self.client.post(f"/api/jobs/{created['id']}/cancel")
        r = self.client.post(f"/api/jobs/{created['id']}/retry")
        self.assertTrue(r.json()["job"]["overwrite_original"])
        self.assertTrue(r.json()["job"]["overwrite_english"])

    def test_empty_body_retry_inherits_false_overwrite_flags(self):
        created = self.client.post(
            "/api/jobs", json={"video_path": "Show/S01E01.mkv"}).json()["job"]
        self.client.post(f"/api/jobs/{created['id']}/cancel")
        r = self.client.post(f"/api/jobs/{created['id']}/retry", json={})
        self.assertFalse(r.json()["job"]["overwrite_original"])
        self.assertFalse(r.json()["job"]["overwrite_english"])

    def test_explicit_false_overrides_a_true_original(self):
        created = self.client.post("/api/jobs", json={
            "video_path": "Show/S01E01.mkv", "overwrite_original": True,
            "overwrite_english": True}).json()["job"]
        self.client.post(f"/api/jobs/{created['id']}/cancel")
        r = self.client.post(f"/api/jobs/{created['id']}/retry",
                             json={"overwrite_original": False, "overwrite_english": False})
        self.assertFalse(r.json()["job"]["overwrite_original"])
        self.assertFalse(r.json()["job"]["overwrite_english"])


class LanguagesTests(ApiTestCase):
    def test_returns_sorted_nllb_language_codes(self):
        r = self.client.get("/api/languages")
        self.assertEqual(r.status_code, 200)
        languages = r.json()["languages"]
        self.assertEqual(languages, sorted(languages))
        self.assertIn("tr", languages)
        self.assertIn("en", languages)


class SrtTranslationApiTests(MediaRootApiTestCase):
    """video_path is REQUIRED -- the destination (<video stem>.en.srt,
    beside the video) and tvdb_id are both derived from it server-side,
    never supplied by the client directly (see api.py's
    create_srt_translation_job() docstring)."""

    def setUp(self):
        super().setUp()
        # MediaRootApiTestCase's setUp already creates Show/S01E01.mkv.
        (self.root / "in").mkdir()
        (self.root / "in" / "ep.tr.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nMerhaba\n", encoding="utf-8")

    def _body(self, **overrides):
        body = {"video_path": "Show/S01E01.mkv", "source_srt_path": "in/ep.tr.srt"}
        body.update(overrides)
        return body

    def test_non_english_target_lang_rejected_with_422(self):
        r = self.client.post("/api/srt-translations", json=self._body(target_lang="fr"))
        self.assertEqual(r.status_code, 422)
        self.assertTrue(r.json()["detail"][0]["msg"].endswith(
            'target_lang must be "en"; multi-target translation is not supported'))
        self.assertEqual(self.client.get("/api/jobs").json()["total"], 0)

    def test_explicit_english_target_lang_still_accepted(self):
        r = self.client.post("/api/srt-translations", json=self._body(target_lang="en"))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["job"]["destination_srt_path"], "Show/S01E01.en.srt")

    def test_creates_queued_srt_translation_job_with_derived_destination(self):
        r = self.client.post("/api/srt-translations", json=self._body())
        self.assertEqual(r.status_code, 201)
        job = r.json()["job"]
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["job_type"], "srt_translation")
        self.assertEqual(job["source_srt_path"], "in/ep.tr.srt")
        self.assertEqual(job["destination_srt_path"], "Show/S01E01.en.srt")
        self.assertEqual(job["video_path"], "Show/S01E01.mkv")
        self.assertFalse(job["source_is_uploaded"])

    def test_tvdb_id_derived_from_required_video_path(self):
        series_dir = self.root / "Series {tvdb-777}"
        series_dir.mkdir()
        (series_dir / "S01E01.mkv").touch()
        r = self.client.post("/api/srt-translations", json=self._body(
            video_path="Series {tvdb-777}/S01E01.mkv"))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["job"]["tvdb_id"], 777)

    def test_video_path_is_required(self):
        r = self.client.post("/api/srt-translations", json={"source_srt_path": "in/ep.tr.srt"})
        self.assertEqual(r.status_code, 422)

    def test_rejects_nonexistent_video_path(self):
        r = self.client.post("/api/srt-translations", json=self._body(
            video_path="Show/does-not-exist.mkv"))
        self.assertEqual(r.status_code, 400)

    def test_rejects_video_path_traversal(self):
        r = self.client.post("/api/srt-translations", json=self._body(
            video_path="../../etc/passwd"))
        self.assertEqual(r.status_code, 400)

    def test_client_cannot_set_destination_directly(self):
        # No destination_srt_path field exists on the request model at
        # all -- an extra field is simply ignored by Pydantic, not an error,
        # but it must have zero effect on the computed destination.
        r = self.client.post("/api/srt-translations", json=self._body(
            destination_srt_path="somewhere/else.en.srt"))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["job"]["destination_srt_path"], "Show/S01E01.en.srt")

    def test_rejects_missing_source_srt(self):
        r = self.client.post("/api/srt-translations", json=self._body(
            source_srt_path="in/does-not-exist.srt"))
        self.assertEqual(r.status_code, 400)

    def test_rejects_source_path_traversal(self):
        r = self.client.post("/api/srt-translations", json=self._body(
            source_srt_path="../../etc/passwd"))
        self.assertEqual(r.status_code, 400)

    def test_rejects_non_srt_source_extension(self):
        (self.root / "in" / "ep.txt").write_text("not an srt", encoding="utf-8")
        r = self.client.post("/api/srt-translations", json=self._body(source_srt_path="in/ep.txt"))
        self.assertEqual(r.status_code, 400)

    def test_rejects_identical_source_and_destination(self):
        (self.root / "Show" / "S01E01.en.srt").write_text("x", encoding="utf-8")
        r = self.client.post("/api/srt-translations", json=self._body(
            source_srt_path="Show/S01E01.en.srt"))
        self.assertEqual(r.status_code, 400)

    def test_rejects_malformed_source_language_shape(self):
        r = self.client.post("/api/srt-translations", json=self._body(source_lang="TR"))
        self.assertEqual(r.status_code, 422)

    def test_unsupported_but_valid_shaped_language_code_rejected_with_400(self):
        r = self.client.post("/api/srt-translations", json=self._body(source_lang="zz"))
        self.assertEqual(r.status_code, 400)

    def test_destination_conflict_while_active_returns_409(self):
        self.client.post("/api/srt-translations", json=self._body())
        r = self.client.post("/api/srt-translations", json=self._body())
        self.assertEqual(r.status_code, 409)

    def test_neither_source_field_given_is_rejected(self):
        r = self.client.post("/api/srt-translations",
                             json={"video_path": "Show/S01E01.mkv"})
        self.assertEqual(r.status_code, 400)

    def test_both_source_fields_given_is_rejected(self):
        r = self.client.post("/api/srt-translations", json=self._body(source_upload_id="whatever"))
        self.assertEqual(r.status_code, 400)

    def test_job_visible_via_the_shared_get_jobs_endpoint(self):
        created = self.client.post("/api/srt-translations", json=self._body()).json()["job"]
        r = self.client.get(f"/api/jobs/{created['id']}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["job_type"], "srt_translation")

    def test_create_via_upload_id_marks_job_as_uploaded(self):
        upload = self.client.post(
            "/api/srt-uploads",
            files={"file": ("fansub.srt", b"1\n00:00:00,000 --> 00:00:01,000\nHi\n", "text/plain")},
        ).json()
        r = self.client.post("/api/srt-translations", json={
            "video_path": "Show/S01E01.mkv", "source_upload_id": upload["upload_id"]})
        self.assertEqual(r.status_code, 201)
        job = r.json()["job"]
        self.assertTrue(job["source_is_uploaded"])
        self.assertEqual(job["source_srt_path"], f"{upload['upload_id']}.srt")
        self.assertEqual(job["destination_srt_path"], "Show/S01E01.en.srt")


class SrtUploadTests(MediaRootApiTestCase):
    def test_valid_upload_returns_id_and_original_filename(self):
        r = self.client.post(
            "/api/srt-uploads",
            files={"file": ("My Subtitle.srt",
                            b"1\n00:00:00,000 --> 00:00:01,000\nMerhaba\n", "text/plain")},
        )
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual(body["filename"], "My Subtitle.srt")
        self.assertTrue(body["upload_id"])

    def test_non_srt_extension_rejected(self):
        r = self.client.post(
            "/api/srt-uploads",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        self.assertEqual(r.status_code, 400)

    def test_oversized_upload_rejected(self):
        huge = b"x" * (2 * 1024 * 1024 + 1)
        r = self.client.post(
            "/api/srt-uploads",
            files={"file": ("big.srt", huge, "text/plain")},
        )
        self.assertEqual(r.status_code, 413)

    def test_non_utf8_content_rejected(self):
        r = self.client.post(
            "/api/srt-uploads",
            files={"file": ("bad.srt", b"\xff\xfe\x00\x01not utf8", "text/plain")},
        )
        self.assertEqual(r.status_code, 400)

    def test_client_supplied_filename_is_never_used_as_a_path(self):
        r = self.client.post(
            "/api/srt-uploads",
            files={"file": ("../../etc/passwd.srt", b"1\n00:00:00,000 --> 00:00:01,000\nHi\n",
                            "text/plain")},
        )
        self.assertEqual(r.status_code, 201)
        # The response's own filename is display-sanitized (basename only);
        # nothing about this request could have written outside the upload dir
        # since the stored name is always a fresh uuid, never derived from it.
        self.assertEqual(r.json()["filename"], "passwd.srt")


class DeleteJobTests(ApiTestCase):
    def test_delete_only_removes_db_row_never_media(self):
        created = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv"}).json()["job"]
        self.client.post(f"/api/jobs/{created['id']}/cancel")
        r = self.client.delete(f"/api/jobs/{created['id']}")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(api.get_store().get(created["id"]))

    def test_cannot_delete_active_job(self):
        created = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv"}).json()["job"]
        r = self.client.delete(f"/api/jobs/{created['id']}")
        self.assertEqual(r.status_code, 409)


class DeleteJobWorkDirTests(unittest.TestCase):
    """DELETE removes the job's scratch directory too (a failed job's is
    otherwise kept for a diagnostic window) -- and nothing else."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work_root = Path(self.tmp.name) / "work"
        self.work_root.mkdir()
        app = api.create_app(Path(self.tmp.name) / "jobs.db", work_root=str(self.work_root))
        self.client = TestClient(app)

    def _failed_job_with_work_dir(self):
        created = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv"}).json()["job"]
        api.get_store().claim()
        api.get_store().finish(created["id"], "failed", error="x")
        work = self.work_root / created["id"]
        work.mkdir()
        (work / "audio.wav").write_bytes(b"x")
        return created["id"], work

    def test_delete_removes_the_jobs_work_dir(self):
        job_id, work = self._failed_job_with_work_dir()
        other = self.work_root / "someone_elses_job"
        other.mkdir()
        r = self.client.delete(f"/api/jobs/{job_id}")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(work.exists())
        self.assertTrue(other.exists())

    def test_delete_of_active_job_is_refused_and_keeps_work_dir(self):
        created = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv"}).json()["job"]
        work = self.work_root / created["id"]
        work.mkdir()
        self.assertEqual(self.client.delete(f"/api/jobs/{created['id']}").status_code, 409)
        self.assertTrue(work.exists())

    def test_delete_succeeds_when_no_work_dir_exists(self):
        job_id, work = self._failed_job_with_work_dir()
        shutil.rmtree(work)
        self.assertEqual(self.client.delete(f"/api/jobs/{job_id}").status_code, 200)

    def test_delete_without_configured_work_root_still_works(self):
        app = api.create_app(Path(self.tmp.name) / "jobs2.db")
        client = TestClient(app)
        created = client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv"}).json()["job"]
        client.post(f"/api/jobs/{created['id']}/cancel")
        self.assertEqual(client.delete(f"/api/jobs/{created['id']}").status_code, 200)


class ConfiguredMediaRootTests(MediaRootApiTestCase):
    """create_job() must validate against the CONFIGURED media root, not
    a hardcoded "/data" -- a real inconsistency this fixes: main.py
    already wired SUBTITLE_AI_MEDIA_ROOT everywhere else (worker.py,
    pipeline.py), but api.py silently ignored it and always checked
    against "/data" regardless of what was actually configured."""

    def test_accepts_a_path_that_only_exists_relative_to_the_configured_root(self):
        r = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv", "source_lang": "tr"})
        self.assertEqual(r.status_code, 201)

    def test_still_rejects_traversal_against_the_configured_root(self):
        r = self.client.post("/api/jobs", json={"video_path": "../../etc/passwd", "source_lang": "tr"})
        self.assertEqual(r.status_code, 400)


class StaticFileTests(unittest.TestCase):
    """The frontend is a Vite-built React SPA (see frontend/) whose output
    (index.html + a content-hashed assets/ directory) is copied into this
    directory by the Dockerfile's frontend-build stage -- these tests use
    a synthetic static_dir fixture rather than depending on a real
    `npm run build` having been run in this environment."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.static_dir = Path(self.tmp.name) / "static"
        (self.static_dir / "assets").mkdir(parents=True)
        (self.static_dir / "index.html").write_text("<html>spa shell</html>", encoding="utf-8")
        (self.static_dir / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
        app = api.create_app(Path(self.tmp.name) / "jobs.db", static_dir=str(self.static_dir))
        self.client = TestClient(app)

    def test_index_served_at_root(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])

    def test_known_asset_served(self):
        r = self.client.get("/assets/index-abc123.js")
        self.assertEqual(r.status_code, 200)

    def test_path_traversal_in_asset_name_rejected(self):
        r = self.client.get("/assets/..%2Findex.html")
        self.assertIn(r.status_code, (400, 404))

    def test_unknown_asset_404s(self):
        r = self.client.get("/assets/does-not-exist.js")
        self.assertEqual(r.status_code, 404)

    def test_client_side_route_falls_back_to_index_html(self):
        r = self.client.get("/series/383383")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])

    def test_fallback_never_shadows_api_routes(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "queue": "sqlite"})

    def test_unknown_api_path_still_404s_not_html(self):
        r = self.client.get("/api/does-not-exist")
        self.assertEqual(r.status_code, 404)


class BrowseTests(MediaRootApiTestCase):
    def test_lists_directories_and_videos_only(self):
        r = self.client.get("/api/browse", params={"path": ""})
        self.assertEqual(r.status_code, 200)
        entries = r.json()["entries"]
        self.assertEqual([e["name"] for e in entries], ["Show"])
        self.assertEqual(entries[0]["type"], "directory")

    def test_non_video_files_excluded(self):
        r = self.client.get("/api/browse", params={"path": "Show"})
        names = [e["name"] for e in r.json()["entries"]]
        self.assertIn("S01E01.mkv", names)
        self.assertNotIn("notes.txt", names)

    def test_path_traversal_rejected(self):
        r = self.client.get("/api/browse", params={"path": "../../etc"})
        self.assertEqual(r.status_code, 400)

    def test_missing_directory_rejected(self):
        r = self.client.get("/api/browse", params={"path": "DoesNotExist"})
        self.assertEqual(r.status_code, 400)

    def test_file_path_rejected_not_a_directory(self):
        r = self.client.get("/api/browse", params={"path": "Show/S01E01.mkv"})
        self.assertEqual(r.status_code, 400)

    def test_srt_file_type_lists_srt_files_not_videos(self):
        (self.root / "Show" / "S01E01.tr.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n",
                                                           encoding="utf-8")
        r = self.client.get("/api/browse", params={"path": "Show", "file_type": "srt"})
        names = [e["name"] for e in r.json()["entries"]]
        self.assertIn("S01E01.tr.srt", names)
        self.assertNotIn("S01E01.mkv", names)

    def test_srt_file_type_entries_have_srt_type(self):
        (self.root / "Show" / "S01E01.tr.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n",
                                                           encoding="utf-8")
        r = self.client.get("/api/browse", params={"path": "Show", "file_type": "srt"})
        entry = next(e for e in r.json()["entries"] if e["name"] == "S01E01.tr.srt")
        self.assertEqual(entry["type"], "srt")

    def test_default_file_type_is_still_video_backward_compatible(self):
        (self.root / "Show" / "S01E01.tr.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n",
                                                           encoding="utf-8")
        r = self.client.get("/api/browse", params={"path": "Show"})
        names = [e["name"] for e in r.json()["entries"]]
        self.assertIn("S01E01.mkv", names)
        self.assertNotIn("S01E01.tr.srt", names)


class MediaMetadataTests(MediaRootApiTestCase):
    def test_non_video_extension_rejected(self):
        r = self.client.get("/api/media", params={"path": "Show/notes.txt"})
        self.assertEqual(r.status_code, 400)

    def test_missing_file_rejected(self):
        r = self.client.get("/api/media", params={"path": "Show/missing.mkv"})
        self.assertEqual(r.status_code, 400)

    def test_path_traversal_rejected(self):
        r = self.client.get("/api/media", params={"path": "../../etc/passwd"})
        self.assertEqual(r.status_code, 400)

    def test_existing_subtitles_detected(self):
        (self.root / "Show" / "S01E01.tr.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n")
        (self.root / "Show" / "S01E01.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\ny\n")
        with patch("media.subprocess.run") as run:
            run.return_value.stdout = '{"format": {"duration": "120.5"}, "streams": []}'
            r = self.client.get("/api/media", params={"path": "Show/S01E01.mkv"})
        self.assertEqual(r.status_code, 200)
        subs = r.json()["existing_subtitles"]
        self.assertIn("Show/S01E01.en.srt", subs)
        self.assertIn("Show/S01E01.tr.srt", subs)

    def test_valid_video_returns_metadata_shape(self):
        with patch("media.subprocess.run") as run:
            run.return_value.stdout = ('{"format": {"duration": "3600"}, "streams": '
                                       '[{"index": 1, "codec_type": "audio", "codec_name": "aac", '
                                       '"channels": 2, "disposition": {"default": 1}, '
                                       '"tags": {"language": "tur"}}]}')
            r = self.client.get("/api/media", params={"path": "Show/S01E01.mkv"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["duration"], 3600.0)
        self.assertEqual(len(data["audio_tracks"]), 1)
        self.assertEqual(data["audio_tracks"][0]["language"], "tur")
        self.assertTrue(data["audio_tracks"][0]["default"])

    def test_audio_tracks_include_full_metadata_not_just_the_narrow_old_set(self):
        with patch("media.subprocess.run") as run:
            run.return_value.stdout = ('{"format": {"duration": "3600"}, "streams": '
                                       '[{"index": 1, "codec_type": "audio", "codec_name": "opus", '
                                       '"channel_layout": "stereo", "bit_rate": "128000", '
                                       '"channels": 2, "disposition": {"comment": 1}, '
                                       '"tags": {"language": "tur", "title": "Commentary"}}]}')
            r = self.client.get("/api/media", params={"path": "Show/S01E01.mkv"})
        track = r.json()["audio_tracks"][0]
        self.assertEqual(track["channel_layout"], "stereo")
        self.assertEqual(track["bit_rate"], 128000)
        self.assertTrue(track["commentary"])
        self.assertIsNotNone(track["exclusion_reason"])


class AudioStreamEndpointTests(MediaRootApiTestCase):
    def _fake_recommendation(self):
        s1 = audio_streams.AudioStream(index=1, language="tur", default=True, channels=2)
        c1 = audio_streams.StreamCandidate(stream=s1, detected_language="tr", detection_confidence=0.95,
                                           score=0.9, reason="detected tr at 95% confidence")
        return audio_streams.StreamRecommendation(
            streams=[s1], ranked=[c1], recommended_index=1, recommended_language="tr",
            recommended_confidence=0.95, reason="detected tr at 95% confidence")

    def test_returns_recommendation_shape(self):
        with patch("api.get_stream_sampler", return_value=lambda wav: ("tr", 0.9)), \
             patch("api.audio_streams.recommend_stream", return_value=self._fake_recommendation()):
            r = self.client.get("/api/audio-streams", params={"path": "Show/S01E01.mkv"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["recommended_index"], 1)
        self.assertEqual(data["recommended_language"], "tr")
        self.assertEqual(len(data["streams"]), 1)
        self.assertEqual(len(data["ranked"]), 1)

    def test_path_traversal_rejected(self):
        r = self.client.get("/api/audio-streams", params={"path": "../../etc/passwd"})
        self.assertEqual(r.status_code, 400)

    def test_non_video_rejected(self):
        r = self.client.get("/api/audio-streams", params={"path": "Show/notes.txt"})
        self.assertEqual(r.status_code, 400)

    def test_media_error_becomes_422_not_a_500(self):
        with patch("api.get_stream_sampler", return_value=lambda wav: ("tr", 0.9)), \
             patch("api.audio_streams.recommend_stream", side_effect=MediaError("no audio stream found")):
            r = self.client.get("/api/audio-streams", params={"path": "Show/S01E01.mkv"})
        self.assertEqual(r.status_code, 422)


class SeriesApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.glossary_dir = Path(self.tmp.name) / "glossary"
        self.glossary_dir.mkdir()
        (self.glossary_dir / "test-series-tr-en.yaml").write_text(SERIES_GLOSSARY_YAML, encoding="utf-8")
        self.suggestions_dir = Path(self.tmp.name) / "suggestions"
        app = api.create_app(Path(self.tmp.name) / "jobs.db",
                             glossary_dir=str(self.glossary_dir),
                             glossary_suggestions_dir=str(self.suggestions_dir))
        self.client = TestClient(app)


class SeriesListTests(SeriesApiTestCase):
    def test_lists_series_grouped_by_tvdb_id(self):
        self.client.post("/api/jobs", json={"video_path": "Show {tvdb-111}/S01E01.mkv"})
        self.client.post("/api/jobs", json={"video_path": "Show {tvdb-111}/S01E02.mkv"})
        r = self.client.get("/api/series")
        self.assertEqual(r.status_code, 200)
        entry = next(s for s in r.json()["series"] if s["tvdb_id"] == 111)
        self.assertEqual(entry["total"], 2)
        self.assertEqual(entry["title"], "Test Series")

    def test_untagged_jobs_bucket_under_none_with_no_title(self):
        self.client.post("/api/jobs", json={"video_path": "Untagged/S01E01.mkv"})
        r = self.client.get("/api/series")
        entry = next(s for s in r.json()["series"] if s["tvdb_id"] is None)
        self.assertIsNone(entry["title"])


class SeriesDetailTests(SeriesApiTestCase):
    def test_returns_episodes_and_manual_glossary(self):
        self.client.post("/api/jobs", json={"video_path": "Show {tvdb-111}/S01E01.mkv"})
        r = self.client.get("/api/series/111")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["title"], "Test Series")
        self.assertEqual(len(data["jobs"]), 1)
        self.assertEqual(data["manual_glossary"][0]["canonical"], "Eda")

    def test_no_jobs_for_tvdb_id_returns_empty_list_not_404(self):
        r = self.client.get("/api/series/999999")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["jobs"], [])

    def test_auto_suggestions_read_from_suggestions_file(self):
        self.suggestions_dir.mkdir(parents=True)
        (self.suggestions_dir / "111.yaml").write_text(
            "tvdb_id: 111\ntitle: null\nentities:\n"
            "- canonical: Melek\n  aliases: []\n  protected: false\n"
            "  occurrences: 9\n  distinct_episodes: 3\n", encoding="utf-8")
        r = self.client.get("/api/series/111")
        suggestions = r.json()["auto_suggestions"]
        self.assertEqual(suggestions[0]["canonical"], "Melek")

    def test_no_suggestions_file_yields_empty_list(self):
        r = self.client.get("/api/series/111")
        self.assertEqual(r.json()["auto_suggestions"], [])


class PromoteGlossaryEntityTests(SeriesApiTestCase):
    def test_promotes_new_name_into_existing_series_file(self):
        r = self.client.post("/api/series/111/glossary/promote", json={"canonical": "Melek"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Melek", [e["canonical"] for e in r.json()["manual_glossary"]])
        # And it's really on disk, not just in the response.
        detail = self.client.get("/api/series/111").json()
        self.assertIn("Melek", [e["canonical"] for e in detail["manual_glossary"]])
        # The pre-existing entity from SERIES_GLOSSARY_YAML is untouched.
        self.assertIn("Eda", [e["canonical"] for e in detail["manual_glossary"]])

    def test_promotes_with_aliases(self):
        r = self.client.post("/api/series/111/glossary/promote",
                             json={"canonical": "Melek", "aliases": ["Melek Hanım"]})
        detail = self.client.get("/api/series/111").json()
        entry = next(e for e in detail["manual_glossary"] if e["canonical"] == "Melek")
        self.assertIn("Melek Hanım", entry["surface_forms"])

    def test_creates_new_file_when_series_has_none_yet(self):
        r = self.client.post("/api/series/222/glossary/promote", json={"canonical": "Ada"})
        self.assertEqual(r.status_code, 200)
        detail = self.client.get("/api/series/222").json()
        self.assertIn("Ada", [e["canonical"] for e in detail["manual_glossary"]])
        # No collision with the unrelated series 111.
        self.assertNotIn("Ada", [e["canonical"]
                                 for e in self.client.get("/api/series/111").json()["manual_glossary"]])

    def test_idempotent_repromote_of_already_protected_name(self):
        self.client.post("/api/series/111/glossary/promote", json={"canonical": "Eda"})
        detail = self.client.get("/api/series/111").json()
        canonicals = [e["canonical"] for e in detail["manual_glossary"]]
        self.assertEqual(canonicals.count("Eda"), 1)

    def test_rejects_empty_canonical(self):
        r = self.client.post("/api/series/111/glossary/promote", json={"canonical": ""})
        self.assertEqual(r.status_code, 422)


class UpdateGlossaryEntityTests(SeriesApiTestCase):
    def test_renames_canonical_and_replaces_aliases(self):
        r = self.client.post("/api/series/111/glossary/update",
                             json={"original_canonical": "Eda", "canonical": "Eda Yıldız",
                                   "aliases": ["Eda"]})
        self.assertEqual(r.status_code, 200)
        detail = self.client.get("/api/series/111").json()
        canonicals = [e["canonical"] for e in detail["manual_glossary"]]
        self.assertIn("Eda Yıldız", canonicals)
        self.assertNotIn("Eda", canonicals)
        entry = next(e for e in detail["manual_glossary"] if e["canonical"] == "Eda Yıldız")
        self.assertIn("Eda", entry["surface_forms"])

    def test_404_for_unknown_original_canonical(self):
        r = self.client.post("/api/series/111/glossary/update",
                             json={"original_canonical": "Nobody", "canonical": "Somebody"})
        self.assertEqual(r.status_code, 404)

    def test_404_when_series_has_no_glossary_file(self):
        r = self.client.post("/api/series/999999/glossary/update",
                             json={"original_canonical": "Eda", "canonical": "Eda Yıldız"})
        self.assertEqual(r.status_code, 404)

    def test_rejects_empty_canonical(self):
        r = self.client.post("/api/series/111/glossary/update",
                             json={"original_canonical": "Eda", "canonical": ""})
        self.assertEqual(r.status_code, 422)


class DeleteGlossaryEntityTests(SeriesApiTestCase):
    def test_removes_entity_from_series_glossary(self):
        r = self.client.post("/api/series/111/glossary/delete", json={"canonical": "Eda"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("Eda", [e["canonical"] for e in r.json()["manual_glossary"]])
        detail = self.client.get("/api/series/111").json()
        self.assertNotIn("Eda", [e["canonical"] for e in detail["manual_glossary"]])

    def test_404_for_unknown_canonical(self):
        r = self.client.post("/api/series/111/glossary/delete", json={"canonical": "Nobody"})
        self.assertEqual(r.status_code, 404)

    def test_404_when_series_has_no_glossary_file(self):
        r = self.client.post("/api/series/999999/glossary/delete", json={"canonical": "Eda"})
        self.assertEqual(r.status_code, 404)


class EventStreamTests(ApiTestCase):
    # No test hits GET /api/events over real HTTP: its generator blocks
    # forever on an empty queue by design (a live SSE connection has no
    # natural end), and TestClient's streaming transport does not return
    # control back to the test until the generator yields or the
    # connection is torn down -- confirmed by direct reproduction, this
    # hangs the whole suite rather than failing fast. events.py's own
    # test suite covers EventBus correctness (including cross-thread
    # delivery) in full; this test covers the one thing that's actually
    # new here -- that create_app() wires JobStore mutations to it.
    def test_creating_a_job_publishes_a_change_event(self):
        async def run():
            api.get_event_bus().bind_loop(asyncio.get_running_loop())
            queue = api.get_event_bus().subscribe()
            try:
                self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv"})
                event = await asyncio.wait_for(queue.get(), timeout=1)
                self.assertEqual(event["type"], "job_changed")
            finally:
                api.get_event_bus().unsubscribe(queue)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
