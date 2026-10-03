import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import api
import errors
import output
import retime_job
import srt
import worker as worker_mod
from jobstore import JobStore, JobStoreError
from test_retime import make_programme
from worker import Worker


def write_subtitle(path: Path, cues, shift: float, protect_text=True) -> None:
    def two_lines(text):
        words = text.split()
        half = len(words) // 2
        return [" ".join(words[:half]), " ".join(words[half:])]
    path.write_text(srt.render([srt.SrtCueLines(c.start + shift, c.end + shift, two_lines(c.text)) for c in cues]),
                    encoding="utf-8")


class RetimeFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.media_root = base / "media"
        self.work_root = base / "work"
        self.cache = base / "cache"
        self.uploads = base / "uploads"
        for d in (self.media_root / "Show", self.cache, self.uploads):
            d.mkdir(parents=True)
        self.video = self.media_root / "Show" / "S01E01.mkv"
        self.video.touch()
        self.truth, words = make_programme(n=200)
        (self.cache / "t.json").write_text(json.dumps({
            "media_path": str(self.video), "language": "tr", "created_at": 1,
            "segments": [{"suppressed": False, "words": [{"text": w.text, "start": w.start, "end": w.end}
                                                         for w in words]}]}))
        write_subtitle(self.media_root / "Show" / "S01E01.tr.srt", self.truth, 5.1)
        self.store = JobStore(base / "jobs.db")
        self.worker = Worker(self.store, str(self.media_root), str(self.work_root),
                             transcript_cache_dir=str(self.cache), srt_upload_dir=str(self.uploads))

    def run_job(self, **kwargs):
        job = self.store.create_retime("Show/S01E01.tr.srt", kwargs.pop("destination", "Show/S01E01.tr.retimed.srt"),
                                       video_path="Show/S01E01.mkv", language="tr", **kwargs)
        claimed = self.store.claim()
        self.assertEqual(claimed["id"], job["id"])
        self.worker._process(claimed)
        return self.store.get(job["id"])


class WorkerTests(RetimeFixture):
    def test_writes_the_retimed_copy_and_leaves_the_original(self):
        original = (self.media_root / "Show" / "S01E01.tr.srt").read_text(encoding="utf-8")
        final = self.run_job()
        self.assertEqual(final["status"], "completed")
        out = self.media_root / "Show" / "S01E01.tr.retimed.srt"
        self.assertEqual(final["outputs"], [str(out)])
        fixed = srt.parse_lines(out)
        self.assertLess(max(abs(f.start - t.start) for f, t in zip(fixed, self.truth)), 0.2)
        self.assertEqual((self.media_root / "Show" / "S01E01.tr.srt").read_text(encoding="utf-8"), original)
        self.assertTrue(any("constant offset" in e["message"] for e in final["log"]))
        self.assertEqual(final["progress"], 100)

    def test_replace_original_overwrites_the_library_subtitle(self):
        final = self.run_job(destination="Show/S01E01.tr.srt", replace_original=True)
        self.assertEqual(final["status"], "completed")
        fixed = srt.parse_lines(self.media_root / "Show" / "S01E01.tr.srt")
        self.assertLess(abs(fixed[0].start - self.truth[0].start), 0.2)
        self.assertFalse((self.media_root / "Show" / "S01E01.tr.retimed.srt").exists())

    def test_aligned_subtitle_completes_without_writing(self):
        write_subtitle(self.media_root / "Show" / "S01E01.tr.srt", self.truth, 0.0)
        final = self.run_job()
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["outputs"], [])
        self.assertFalse((self.media_root / "Show" / "S01E01.tr.retimed.srt").exists())

    def test_wrong_programme_fails_with_its_own_category_and_writes_nothing(self):
        other, _ = make_programme(n=200, seed=77)
        (self.media_root / "Show" / "S01E01.tr.srt").write_text(
            srt.render([srt.SrtCueLines(c.start, c.end, [c.text.replace("kelime", "baska")]) for c in other]),
            encoding="utf-8")
        final = self.run_job()
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["error_category"], "RETIME_REFUSED")
        self.assertFalse((self.media_root / "Show" / "S01E01.tr.retimed.srt").exists())

    def test_uploaded_source_is_read_from_the_upload_dir(self):
        write_subtitle(self.uploads / "abc.srt", self.truth, -4.0)
        job = self.store.create_retime("abc.srt", "Show/S01E01.tr.retimed.srt", video_path="Show/S01E01.mkv",
                                       language="tr", source_is_uploaded=True)
        self.worker._process(self.store.claim())
        self.assertEqual(self.store.get(job["id"])["status"], "completed")

    def test_missing_source_fails_with_output_error(self):
        self.store.create_retime("Show/gone.tr.srt", "Show/S01E01.tr.retimed.srt", video_path="Show/S01E01.mkv",
                                 language="tr")
        claimed = self.store.claim()
        self.worker._process(claimed)
        self.assertEqual(self.store.get(claimed["id"])["error_category"], "OUTPUT_ERROR")

    def test_does_not_touch_the_transcript_cache(self):
        before = sorted(p.name for p in self.cache.iterdir())
        self.run_job()
        self.assertEqual(sorted(p.name for p in self.cache.iterdir()), before)

    def test_transcribes_the_audio_when_no_transcript_is_cached(self):
        (self.cache / "t.json").unlink()
        _, words = make_programme(n=200)
        fresh = [retime_job.TimedWord(w.text, w.start, w.end) for w in words]
        with patch.object(retime_job, "transcribe_words", return_value=fresh) as mock_asr:
            final = self.run_job()
        mock_asr.assert_called_once()
        self.assertEqual(final["status"], "completed")
        self.assertTrue(any("transcript from fresh" in e["message"] for e in final["log"]))

    def test_never_runs_the_translation_pipelines(self):
        with patch.object(worker_mod.pipeline, "run") as video, \
             patch.object(worker_mod.srt_translation, "run_srt_translation_pipeline") as translation:
            self.run_job()
        video.assert_not_called()
        translation.assert_not_called()


class StoreTests(RetimeFixture):
    def test_creates_a_retime_job(self):
        job = self.store.create_retime("Show/S01E01.tr.srt", "Show/S01E01.tr.retimed.srt",
                                       video_path="Show/S01E01.mkv", language="tr")
        self.assertEqual((job["job_type"], job["status"], job["source_lang"]), ("subtitle_retime", "queued", "tr"))
        self.assertEqual(job["destination_srt_path"], "Show/S01E01.tr.retimed.srt")

    def test_second_active_job_for_the_same_destination_is_refused(self):
        args = ("Show/S01E01.tr.srt", "Show/S01E01.tr.retimed.srt")
        self.store.create_retime(*args, video_path="Show/S01E01.mkv", language="tr")
        with self.assertRaises(JobStoreError):
            self.store.create_retime(*args, video_path="Show/S01E01.mkv", language="tr")

    def test_does_not_collide_with_a_translation_job_for_the_same_video(self):
        self.store.create_srt_translation("Show/S01E01.tr.srt", "Show/S01E01.en.srt", video_path="Show/S01E01.mkv")
        self.store.create_retime("Show/S01E01.tr.srt", "Show/S01E01.tr.retimed.srt",
                                 video_path="Show/S01E01.mkv", language="tr")

    def test_retry_stays_a_retime_job_with_the_same_destination(self):
        final = self.run_job(replace_original=True, destination="Show/S01E01.tr.srt")
        again = self.store.retry(final["id"])
        self.assertEqual((again["job_type"], again["destination_srt_path"], again["attempt"]),
                         ("subtitle_retime", "Show/S01E01.tr.srt", 2))
        self.assertTrue(again["overwrite_original"])


class FilenameTests(unittest.TestCase):
    def test_language_from_filename(self):
        self.assertEqual(retime_job.language_from_filename("Film.tr.srt"), "tr")
        self.assertEqual(retime_job.language_from_filename("Film.zh.retimed.srt"), "zh")
        self.assertIsNone(retime_job.language_from_filename("Film.srt"))
        self.assertIsNone(retime_job.language_from_filename("Film.English.srt"))


class OutputPathTests(unittest.TestCase):
    def test_retimed_path_sits_beside_the_video(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            self.assertEqual(output.resolve_retimed_path(root, "Show/S01E01.mkv", "tr"),
                             root / "Show" / "S01E01.tr.retimed.srt")

    def test_escaping_the_root_and_bad_languages_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(errors.OutputSafetyError):
                output.resolve_retimed_path(d, "../x.mkv", "tr")
            with self.assertRaises(errors.OutputSafetyError):
                output.resolve_retimed_path(d, "Show/a.mkv", "TR!")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.root = base / "media"
        (self.root / "Show").mkdir(parents=True)
        (self.root / "Show" / "S01E01.mkv").write_bytes(b"x")
        (self.root / "Show" / "S01E01.tr.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nMerhaba\n", encoding="utf-8")
        (self.root / "Show" / "S01E01.en.hi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
        (self.root / "Show" / "notes.txt").write_text("x", encoding="utf-8")
        self.uploads = base / "uploads"
        self.client = TestClient(api.create_app(base / "jobs.db", str(self.root), srt_upload_dir=str(self.uploads)))

    def post(self, **overrides):
        body = {"video_path": "Show/S01E01.mkv", "source_srt_path": "Show/S01E01.tr.srt"}
        body.update(overrides)
        return self.client.post("/api/subtitle-retimes", json=body)

    def test_creates_a_job_writing_the_retimed_sidecar_by_default(self):
        r = self.post()
        self.assertEqual(r.status_code, 201)
        job = r.json()["job"]
        self.assertEqual((job["job_type"], job["source_lang"], job["destination_srt_path"], job["video_path"]),
                         ("subtitle_retime", "tr", "Show/S01E01.tr.retimed.srt", "Show/S01E01.mkv"))
        self.assertFalse(job["overwrite_original"])

    def test_replace_original_targets_the_library_subtitle(self):
        job = self.post(replace_original=True).json()["job"]
        self.assertEqual(job["destination_srt_path"], "Show/S01E01.tr.srt")
        self.assertTrue(job["overwrite_original"])

    def test_explicit_language_overrides_the_file_name(self):
        (self.root / "Show" / "plain.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
        self.assertEqual(self.post(source_srt_path="Show/plain.srt").status_code, 400)
        r = self.post(source_srt_path="Show/plain.srt", language="ja")
        self.assertEqual(r.json()["job"]["destination_srt_path"], "Show/S01E01.ja.retimed.srt")

    def test_upload_source(self):
        up = self.client.post("/api/srt-uploads", files={"file": ("x.srt", b"1\n00:00:00,000 --> 00:00:01,000\nMerhaba\n")})
        upload_id = up.json()["upload_id"]
        self.assertEqual(self.post(source_srt_path=None, source_upload_id=upload_id).status_code, 400)  # no language
        r = self.post(source_srt_path=None, source_upload_id=upload_id, language="tr")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.json()["job"]["source_is_uploaded"])

    def test_needs_exactly_one_source(self):
        self.assertEqual(self.post(source_upload_id="abc").status_code, 400)
        self.assertEqual(self.post(source_srt_path=None).status_code, 400)

    def test_rejects_bad_inputs(self):
        self.assertEqual(self.post(video_path="Show/missing.mkv").status_code, 400)
        self.assertEqual(self.post(video_path="Show/notes.txt").status_code, 400)
        self.assertEqual(self.post(video_path="../etc/passwd").status_code, 400)
        self.assertEqual(self.post(source_srt_path="Show/notes.txt").status_code, 400)
        self.assertEqual(self.post(source_srt_path="Show/nope.tr.srt").status_code, 400)
        self.assertEqual(self.post(language="TR").status_code, 422)
        self.assertEqual(self.client.get("/api/jobs").json()["total"], 0)

    def test_protected_external_subtitle_is_never_a_source(self):
        self.assertEqual(self.post(source_srt_path="Show/S01E01.en.hi.srt", language="en").status_code, 400)

    def test_duplicate_active_job_is_a_conflict(self):
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(self.post().status_code, 409)

    def test_retimed_copy_is_the_job_target_for_the_editor(self):
        job = self.post().json()["job"]
        (self.root / "Show" / "S01E01.tr.retimed.srt").write_text(
            "1\n00:00:05,000 --> 00:00:06,000\nMerhaba\n", encoding="utf-8")
        r = self.client.get(f"/api/jobs/{job['id']}/srt")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["cues"][0]["start"], 5.0)


if __name__ == "__main__":
    unittest.main()
