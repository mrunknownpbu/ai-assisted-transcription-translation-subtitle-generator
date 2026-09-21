"""workdir.py: per-job scratch-directory cleanup and the stale sweep.
The safety properties matter most -- cleanup must never be able to
remove anything outside WORK_ROOT's direct children."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import workdir
from jobstore import JobStore

HOUR = 3600.0


class WorkdirTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "work"
        self.root.mkdir()
        self.store = JobStore(self.base / "jobs.db")

    def make_dir(self, name: str, with_file: bool = True) -> Path:
        d = self.root / name
        d.mkdir()
        if with_file:
            (d / "audio.wav").write_bytes(b"x" * 16)
        return d

    def make_job(self, status: str, *, finished_hours_ago: float = 0.0) -> str:
        self._n = getattr(self, "_n", 0) + 1
        job = self.store.create(f"Show/S01E{self._n:02d}.mkv", "tr")
        if status == "running":
            self.store.claim()
        elif status != "queued":
            self.store.claim()
            self.store.finish(job["id"], status)
            self.store.update(job["id"], finished_at=time.time() - finished_hours_ago * HOUR)
        return job["id"]


class CleanupWorkDirTests(WorkdirTestCase):
    def test_removes_the_job_directory_and_its_contents(self):
        d = self.make_dir("abc123")
        (d / "sub").mkdir()
        (d / "sub" / "clip.wav").write_bytes(b"y")
        self.assertTrue(workdir.cleanup_work_dir(self.root, "abc123"))
        self.assertFalse(d.exists())
        self.assertTrue(self.root.exists())

    def test_missing_directory_is_a_quiet_false(self):
        self.assertFalse(workdir.cleanup_work_dir(self.root, "nope"))

    def test_leaves_sibling_job_directories_alone(self):
        keep = self.make_dir("keepme")
        self.make_dir("gone")
        workdir.cleanup_work_dir(self.root, "gone")
        self.assertTrue(keep.exists())

    def test_malformed_job_ids_are_refused_and_nothing_outside_is_touched(self):
        victim = self.base / "victim"
        victim.mkdir()
        (victim / "precious.txt").write_text("keep")
        for bad in ("../victim", "..", ".", "", "a/b", "/etc", str(victim), "x\x00y", "a b"):
            with self.subTest(job_id=bad):
                self.assertFalse(workdir.cleanup_work_dir(self.root, bad))
        self.assertTrue((victim / "precious.txt").exists())
        self.assertTrue(self.root.exists())

    def test_non_string_job_id_is_refused(self):
        self.assertFalse(workdir.cleanup_work_dir(self.root, None))

    def test_symlinked_job_directory_is_refused_and_target_survives(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "precious.txt").write_text("keep")
        link = self.root / "linkjob"
        link.symlink_to(outside, target_is_directory=True)
        self.assertFalse(workdir.cleanup_work_dir(self.root, "linkjob"))
        self.assertTrue((outside / "precious.txt").exists())
        self.assertTrue(link.is_symlink())

    def test_symlink_inside_a_job_directory_is_removed_without_following_it(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "precious.txt").write_text("keep")
        d = self.make_dir("job1")
        (d / "link").symlink_to(outside, target_is_directory=True)
        self.assertTrue(workdir.cleanup_work_dir(self.root, "job1"))
        self.assertFalse(d.exists())
        self.assertTrue((outside / "precious.txt").exists())

    def test_symlinked_work_root_still_only_removes_direct_children(self):
        real = self.base / "real_work"
        real.mkdir()
        (real / "job1").mkdir()
        alias = self.base / "alias_work"
        alias.symlink_to(real, target_is_directory=True)
        self.assertTrue(workdir.cleanup_work_dir(alias, "job1"))
        self.assertFalse((real / "job1").exists())

    def test_oserror_is_swallowed_and_reported_as_false(self):
        self.make_dir("job1")
        with patch("workdir.shutil.rmtree", side_effect=PermissionError("nope")):
            self.assertFalse(workdir.cleanup_work_dir(self.root, "job1"))

    def test_a_plain_file_named_like_a_job_is_not_removed(self):
        f = self.root / "job1"
        f.write_text("not a dir")
        self.assertFalse(workdir.cleanup_work_dir(self.root, "job1"))
        self.assertTrue(f.exists())


class SweepStaleTests(WorkdirTestCase):
    def sweep(self, hours: float = 24.0) -> list[str]:
        return workdir.sweep_stale(self.root, self.store, hours)

    def test_never_removes_queued_or_running_jobs(self):
        # make_job("running") claims the oldest queued job, so it goes first.
        running = self.make_job("running")
        queued = self.make_job("queued")
        d_q, d_r = self.make_dir(queued), self.make_dir(running)
        old = time.time() - 1000 * HOUR
        for d in (d_q, d_r):
            os.utime(d, (old, old))
        self.assertEqual(self.sweep(), [])
        self.assertTrue(d_q.exists() and d_r.exists())

    def test_removes_completed_and_cancelled_regardless_of_age(self):
        done = self.make_job("completed")
        cancelled = self.make_job("cancelled")
        d1, d2 = self.make_dir(done), self.make_dir(cancelled)
        self.assertCountEqual(self.sweep(), [done, cancelled])
        self.assertFalse(d1.exists() or d2.exists())

    def test_keeps_failed_job_inside_the_retention_window(self):
        failed = self.make_job("failed", finished_hours_ago=23)
        d = self.make_dir(failed)
        self.assertEqual(self.sweep(24), [])
        self.assertTrue(d.exists())

    def test_removes_failed_job_after_the_retention_window(self):
        failed = self.make_job("failed", finished_hours_ago=25)
        d = self.make_dir(failed)
        self.assertEqual(self.sweep(24), [failed])
        self.assertFalse(d.exists())

    def test_zero_retention_removes_failed_immediately(self):
        failed = self.make_job("failed", finished_hours_ago=0.001)
        self.make_dir(failed)
        self.assertEqual(self.sweep(0), [failed])

    def test_orphan_directory_is_aged_by_mtime(self):
        fresh, stale = self.make_dir("orphan_fresh"), self.make_dir("orphan_stale")
        old = time.time() - 48 * HOUR
        os.utime(stale, (old, old))
        self.assertEqual(self.sweep(24), ["orphan_stale"])
        self.assertTrue(fresh.exists())

    def test_ignores_files_and_symlinks_in_the_work_root(self):
        (self.root / "stray.txt").write_text("x")
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "precious.txt").write_text("keep")
        (self.root / "linkjob").symlink_to(outside, target_is_directory=True)
        old = time.time() - 1000 * HOUR
        os.utime(self.root / "stray.txt", (old, old))
        self.assertEqual(self.sweep(1), [])
        self.assertTrue((self.root / "stray.txt").exists())
        self.assertTrue((outside / "precious.txt").exists())

    def test_never_touches_siblings_of_the_work_root(self):
        cache = self.base / "transcripts"
        cache.mkdir()
        (cache / "t.json").write_text("{}")
        (self.base / "uploads").mkdir()
        self.make_dir(self.make_job("completed"))
        self.sweep()
        self.assertTrue((cache / "t.json").exists())
        self.assertTrue((self.base / "uploads").exists())
        self.assertTrue((self.base / "jobs.db").exists())

    def test_missing_work_root_is_a_no_op(self):
        self.assertEqual(workdir.sweep_stale(self.base / "absent", self.store, 24), [])

    def test_one_bad_entry_does_not_stop_the_sweep(self):
        a, b = self.make_job("completed"), self.make_job("completed")
        self.make_dir(a), self.make_dir(b)
        real = self.store.get

        def flaky(job_id):
            if job_id == min(a, b):
                raise RuntimeError("db hiccup")
            return real(job_id)

        with patch.object(self.store, "get", side_effect=flaky):
            removed = self.sweep()
        self.assertEqual(removed, [max(a, b)])


class ParseRetentionHoursTests(unittest.TestCase):
    def test_defaults_when_unset_or_blank(self):
        self.assertEqual(workdir.parse_retention_hours(None), 24.0)
        self.assertEqual(workdir.parse_retention_hours("  "), 24.0)

    def test_parses_valid_values_including_zero_and_fractions(self):
        self.assertEqual(workdir.parse_retention_hours("48"), 48.0)
        self.assertEqual(workdir.parse_retention_hours("0"), 0.0)
        self.assertEqual(workdir.parse_retention_hours("0.5"), 0.5)

    def test_invalid_values_fall_back_to_default(self):
        for raw in ("abc", "-1", "nan"):
            with self.subTest(raw=raw):
                self.assertEqual(workdir.parse_retention_hours(raw), 24.0)


if __name__ == "__main__":
    unittest.main()
