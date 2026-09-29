import os
import tempfile
import time
import unittest
from pathlib import Path

from jobstore import JobStore
import upload_cleanup


class SrtUploadCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "uploads"
        self.root.mkdir()
        self.store = JobStore(Path(self.tmp.name) / "jobs.db")
        self.now = time.time()

    def _old_upload(self, name: str) -> Path:
        path = self.root / name
        path.write_text("uploaded", encoding="utf-8")
        old = self.now - upload_cleanup.SRT_UPLOAD_RETENTION_HOURS * 3600 - 1
        os.utime(path, (old, old))
        return path

    def test_removes_old_unreferenced_upload_and_keeps_recent_file(self):
        stale = self._old_upload("a" * 32 + ".srt")
        recent = self.root / ("b" * 32 + ".srt")
        recent.write_text("recent", encoding="utf-8")
        removed = upload_cleanup.sweep_stale(self.root, self.store, now=self.now)
        self.assertEqual(removed, [stale.name])
        self.assertFalse(stale.exists())
        self.assertTrue(recent.exists())

    def test_keeps_old_upload_referenced_by_active_job(self):
        name = "a" * 32 + ".srt"
        path = self._old_upload(name)
        job = self.store.create_srt_translation(
            name, "Show/S01E01.en.srt", source_is_uploaded=True)
        self.assertEqual(upload_cleanup.sweep_stale(self.root, self.store, now=self.now), [])
        self.assertTrue(path.exists())
        claimed = self.store.claim()
        self.assertEqual(claimed["id"], job["id"])
        self.assertEqual(upload_cleanup.sweep_stale(self.root, self.store, now=self.now), [])

    def test_removes_upload_after_referenced_job_is_terminal_and_expired(self):
        name = "a" * 32 + ".srt"
        path = self._old_upload(name)
        job = self.store.create_srt_translation(
            name, "Show/S01E01.en.srt", source_is_uploaded=True)
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "completed")
        self.assertEqual(upload_cleanup.sweep_stale(self.root, self.store, now=self.now), [name])
        self.assertFalse(path.exists())

    def test_ignores_symlinks_directories_and_non_upload_filenames(self):
        target = self._old_upload("c" * 32 + ".srt")
        link = self.root / ("d" * 32 + ".srt")
        link.symlink_to(target)
        directory = self.root / ("e" * 32 + ".srt")
        directory.mkdir()
        unrelated = self._old_upload("notes.srt")
        removed = upload_cleanup.sweep_stale(self.root, self.store, now=self.now)
        self.assertEqual(removed, [target.name])
        self.assertTrue(link.is_symlink())
        self.assertTrue(directory.is_dir())
        self.assertTrue(unrelated.exists())
