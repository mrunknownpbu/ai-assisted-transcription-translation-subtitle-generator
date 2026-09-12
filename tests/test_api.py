import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import api
import audio_streams
from media import MediaError


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
        app = api.create_app(Path(self.tmp.name) / "jobs.db", str(self.root))
        self.client = TestClient(app)


class HealthTests(ApiTestCase):
    def test_health_ok(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])


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

    def test_target_lang_defaults_to_english(self):
        r = self.client.post("/api/jobs", json={"video_path": "Show/S01E01.mkv"})
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


class StaticFileTests(MediaRootApiTestCase):
    def test_index_served_at_root(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])

    def test_known_static_file_served(self):
        r = self.client.get("/static/app.js")
        self.assertEqual(r.status_code, 200)

    def test_unknown_static_file_rejected_not_served_from_disk(self):
        r = self.client.get("/static/../main.py")
        self.assertIn(r.status_code, (404, 307))   # some clients normalize the path before it reaches us

    def test_arbitrary_filename_404s(self):
        r = self.client.get("/static/does-not-exist.txt")
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


if __name__ == "__main__":
    unittest.main()
