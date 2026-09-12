import tempfile
import threading
import time
import unittest
from pathlib import Path

from jobstore import JobStore, JobStoreError


class JobStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = JobStore(Path(self.tmp.name) / "jobs.db")


class CreateAndGetTests(JobStoreTestCase):
    def test_create_returns_queued_job(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["stage"], "QUEUED")
        self.assertIsNone(job["started_at"])

    def test_get_unknown_id_returns_none(self):
        self.assertIsNone(self.store.get("does-not-exist"))


class LanguageModeDefaultsTests(JobStoreTestCase):
    def test_default_source_lang_is_auto(self):
        job = self.store.create("Show/S01E01.mkv")
        self.assertEqual(job["source_lang"], "auto")
        self.assertEqual(job["source_language_mode"], "AUTO")

    def test_explicit_manual_language_sets_mode_manual(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        self.assertEqual(job["source_lang"], "tr")
        self.assertEqual(job["source_language_mode"], "MANUAL")

    def test_explicit_auto_also_sets_mode_auto(self):
        job = self.store.create("Show/S01E01.mkv", "auto")
        self.assertEqual(job["source_language_mode"], "AUTO")

    def test_target_lang_defaults_to_english(self):
        job = self.store.create("Show/S01E01.mkv")
        self.assertEqual(job["target_lang"], "en")

    def test_detected_language_and_confidence_start_unset(self):
        job = self.store.create("Show/S01E01.mkv")
        self.assertIsNone(job["detected_language"])
        self.assertIsNone(job["language_confidence"])

    def test_update_persists_detected_language_mid_job(self):
        job = self.store.create("Show/S01E01.mkv")
        self.store.claim()
        self.store.update(job["id"], detected_language="ko", language_confidence=0.87)
        updated = self.store.get(job["id"])
        self.assertEqual(updated["detected_language"], "ko")
        self.assertEqual(updated["language_confidence"], 0.87)


class AudioStreamSelectionTests(JobStoreTestCase):
    def test_default_stream_selection_is_auto(self):
        job = self.store.create("Show/S01E01.mkv")
        self.assertIsNone(job["requested_audio_stream"])
        self.assertEqual(job["stream_selection_mode"], "AUTO")

    def test_explicit_stream_index_sets_mode_manual(self):
        job = self.store.create("Show/S01E01.mkv", audio_stream_index=1)
        self.assertEqual(job["requested_audio_stream"], 1)
        self.assertEqual(job["stream_selection_mode"], "MANUAL")

    def test_stream_mode_is_independent_of_language_mode(self):
        # AUTO language + MANUAL stream: a real, legitimate combination.
        job = self.store.create("Show/S01E01.mkv", "auto", audio_stream_index=2)
        self.assertEqual(job["source_language_mode"], "AUTO")
        self.assertEqual(job["stream_selection_mode"], "MANUAL")

    def test_update_persists_selected_stream_mid_job(self):
        job = self.store.create("Show/S01E01.mkv")
        self.store.claim()
        self.store.update(job["id"], selected_audio_stream=0, embedded_stream_language="tur",
                          stream_selection_mode="AUTO", selected_stream_reason="detected tr at 95%")
        updated = self.store.get(job["id"])
        self.assertEqual(updated["selected_audio_stream"], 0)
        self.assertEqual(updated["embedded_stream_language"], "tur")
        self.assertEqual(updated["selected_stream_reason"], "detected tr at 95%")

    def test_retry_without_override_carries_requested_stream_forward(self):
        job = self.store.create("Show/S01E01.mkv", audio_stream_index=1)
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        retried = self.store.retry(claimed["id"])
        self.assertEqual(retried["requested_audio_stream"], 1)
        self.assertEqual(retried["stream_selection_mode"], "MANUAL")

    def test_retry_with_explicit_none_forces_auto_reselection(self):
        job = self.store.create("Show/S01E01.mkv", audio_stream_index=1)
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        retried = self.store.retry(claimed["id"], audio_stream_index=None)
        self.assertIsNone(retried["requested_audio_stream"])
        self.assertEqual(retried["stream_selection_mode"], "AUTO")

    def test_retry_with_different_manual_stream_override(self):
        job = self.store.create("Show/S01E01.mkv", audio_stream_index=1)
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        retried = self.store.retry(claimed["id"], audio_stream_index=2)
        self.assertEqual(retried["requested_audio_stream"], 2)


class SchemaMigrationTests(unittest.TestCase):
    """A jobs.db created before this schema version has none of the new
    language-detection columns -- JobStore must add them in place rather
    than erroring or silently ignoring the existing data."""

    def test_opening_a_pre_migration_database_adds_new_columns(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.db"
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY, video_path TEXT NOT NULL, status TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'QUEUED', progress REAL NOT NULL DEFAULT 0,
                    source_lang TEXT, overwrite_original INTEGER NOT NULL DEFAULT 0,
                    overwrite_english INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                    started_at REAL, finished_at REAL, updated_at REAL NOT NULL, error TEXT,
                    error_category TEXT, outputs TEXT NOT NULL DEFAULT '[]',
                    qc TEXT NOT NULL DEFAULT '{}', cancel_requested INTEGER NOT NULL DEFAULT 0,
                    retry_of_job_id TEXT, attempt INTEGER NOT NULL DEFAULT 1,
                    log TEXT NOT NULL DEFAULT '[]'
                )""")
            conn.execute(
                "INSERT INTO jobs (id, video_path, status, created_at, updated_at) "
                "VALUES ('old-job', 'Show/S01E01.mkv', 'completed', 0, 0)")
            conn.commit()
            conn.close()

            store = JobStore(db_path)  # must not raise
            job = store.get("old-job")
            self.assertIsNone(job["detected_language"])
            self.assertEqual(job["target_lang"], "en")
            self.assertEqual(job["source_language_mode"], "AUTO")
            self.assertIsNone(job["requested_audio_stream"])
            self.assertIsNone(job["selected_audio_stream"])
            self.assertEqual(job["stream_selection_mode"], "AUTO")
            # And new jobs on the migrated database work normally.
            new_job = store.create("Show/S01E02.mkv")
            self.assertEqual(new_job["source_lang"], "auto")


class ClaimRaceSafetyTests(JobStoreTestCase):
    def test_single_queued_job_claimed_exactly_once_under_concurrency(self):
        self.store.create("Show/S01E01.mkv", "tr")
        results = []
        errors = []

        def worker():
            try:
                results.append(self.store.claim())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        claimed = [r for r in results if r is not None]
        self.assertEqual(len(claimed), 1, "exactly one worker must claim the single queued job")
        self.assertEqual(sum(1 for r in results if r is None), 7)

    def test_claimed_job_removed_from_queue_immediately(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        self.store.claim()
        queued, _ = self.store.list(status="queued")
        self.assertEqual(queued, [])

    def test_no_queued_job_returns_none(self):
        self.assertIsNone(self.store.claim())


class ElapsedTimeTests(JobStoreTestCase):
    def test_running_elapsed_ticks_with_the_clock(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        claimed = self.store.claim()
        e1 = self.store.get(claimed["id"])["elapsed_seconds"]
        time.sleep(0.05)
        e2 = self.store.get(claimed["id"])["elapsed_seconds"]
        self.assertGreater(e2, e1)

    def test_terminal_elapsed_is_frozen(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        claimed = self.store.claim()
        time.sleep(0.05)
        finished = self.store.finish(claimed["id"], "completed")
        e1 = finished["elapsed_seconds"]
        time.sleep(0.1)
        e2 = self.store.get(claimed["id"])["elapsed_seconds"]
        self.assertEqual(e1, e2, "terminal elapsed must never increase after finish()")

    def test_terminal_elapsed_equals_finished_minus_started(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        claimed = self.store.claim()
        finished = self.store.finish(claimed["id"], "completed")
        self.assertAlmostEqual(finished["elapsed_seconds"],
                               finished["finished_at"] - finished["started_at"], places=3)

    def test_queued_job_has_zero_elapsed(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        self.assertEqual(job["elapsed_seconds"], 0.0)

    def test_finish_requires_terminal_status(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        claimed = self.store.claim()
        with self.assertRaises(JobStoreError):
            self.store.finish(claimed["id"], "running")


class CancelTests(JobStoreTestCase):
    def test_cancel_queued_job_is_immediate(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        result = self.store.request_cancel(job["id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNotNone(result["finished_at"])

    def test_cancel_running_job_sets_flag_not_status(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        claimed = self.store.claim()
        result = self.store.request_cancel(claimed["id"])
        self.assertEqual(result["status"], "running")
        self.assertTrue(result["cancel_requested"])
        self.assertTrue(self.store.is_cancel_requested(claimed["id"]))

    def test_cannot_cancel_a_terminal_job(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "completed")
        with self.assertRaises(JobStoreError):
            self.store.request_cancel(claimed["id"])


class RetryTests(JobStoreTestCase):
    def test_retry_creates_a_new_job_not_mutate_original(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        claimed = self.store.claim()
        original_finished = self.store.finish(claimed["id"], "failed", error="boom")
        retried = self.store.retry(claimed["id"], overwrite_original=True)
        self.assertNotEqual(retried["id"], claimed["id"])
        self.assertEqual(retried["retry_of_job_id"], claimed["id"])
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried["attempt"], 2)
        # Original's terminal record is untouched.
        still_there = self.store.get(claimed["id"])
        self.assertEqual(still_there["status"], "failed")
        self.assertEqual(still_there["finished_at"], original_finished["finished_at"])

    def test_retry_repeats_keep_replace_decision(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        retried = self.store.retry(claimed["id"], overwrite_original=True, overwrite_english=False)
        self.assertTrue(retried["overwrite_original"])
        self.assertFalse(retried["overwrite_english"])

    def test_cannot_retry_a_non_terminal_job(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        self.store.claim()
        with self.assertRaises(JobStoreError):
            self.store.retry(job["id"])

    def test_retry_without_override_carries_requested_language_forward(self):
        job = self.store.create("Show/S01E01.mkv", "auto")
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed", error_category="LOW_CONFIDENCE_LANGUAGE")
        retried = self.store.retry(claimed["id"])
        self.assertEqual(retried["source_lang"], "auto")

    def test_retry_with_manual_override_after_low_confidence_auto_failure(self):
        job = self.store.create("Show/S01E01.mkv", "auto")
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed", error_category="LOW_CONFIDENCE_LANGUAGE")
        retried = self.store.retry(claimed["id"], source_lang="tr")
        self.assertEqual(retried["source_lang"], "tr")
        self.assertEqual(retried["source_language_mode"], "MANUAL")


class CountsTests(JobStoreTestCase):
    def test_counts_reflect_status_distribution(self):
        j1 = self.store.create("a.mkv")
        j2 = self.store.create("b.mkv")
        c1 = self.store.claim()
        self.store.finish(c1["id"], "completed")
        counts = self.store.counts()
        self.assertEqual(counts["ALL"], 2)
        self.assertEqual(counts["COMPLETED"], 1)
        self.assertEqual(counts["QUEUED"], 1)


if __name__ == "__main__":
    unittest.main()


class DuplicateJobPreventionTests(JobStoreTestCase):
    """Adversarial: nothing previously stopped the same video from being
    enqueued twice concurrently, wasting GPU time on a duplicate run."""

    def test_second_create_for_same_active_video_is_refused(self):
        self.store.create("Show/S01E01.mkv", "tr")
        with self.assertRaises(JobStoreError):
            self.store.create("Show/S01E01.mkv", "tr")

    def test_refused_while_running_too(self):
        self.store.create("Show/S01E01.mkv", "tr")
        self.store.claim()
        with self.assertRaises(JobStoreError):
            self.store.create("Show/S01E01.mkv", "tr")

    def test_allowed_again_once_terminal(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "completed")
        second = self.store.create("Show/S01E01.mkv", "tr")  # must not raise
        self.assertNotEqual(second["id"], job["id"])

    def test_different_videos_are_independent(self):
        self.store.create("Show/S01E01.mkv", "tr")
        self.store.create("Show/S01E02.mkv", "tr")  # must not raise

    def test_concurrent_duplicate_submissions_only_one_succeeds(self):
        results = []
        errors = []

        def worker():
            try:
                results.append(self.store.create("Show/S01E01.mkv", "tr"))
            except JobStoreError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 1, "exactly one concurrent create() must succeed")
        self.assertEqual(len(errors), 7)
