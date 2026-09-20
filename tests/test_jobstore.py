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
            self.assertIsNone(job["tvdb_id"])
            # And new jobs on the migrated database work normally.
            new_job = store.create("Show/S01E02.mkv")
            self.assertEqual(new_job["source_lang"], "auto")

    def test_tvdb_id_backfilled_from_video_path_on_migration(self):
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
                "INSERT INTO jobs (id, video_path, status, created_at, updated_at) VALUES "
                "('tagged', 'Show (2020) {tvdb-383383}/S01E01.mkv', 'completed', 0, 0), "
                "('untagged', 'Some Show/S01E01.mkv', 'completed', 0, 0)")
            conn.commit()
            conn.close()

            store = JobStore(db_path)
            self.assertEqual(store.get("tagged")["tvdb_id"], 383383)
            self.assertIsNone(store.get("untagged")["tvdb_id"])

    def test_backfill_runs_even_when_column_already_existed_with_null_rows(self):
        # Real deploy sequence this guards against: the tvdb_id column
        # shipped in one release, the backfill logic in a later one --
        # gating backfill on "column just added" would silently skip
        # every row created in between.
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.db"
            store = JobStore(db_path)
            with store._immediate() as conn:
                conn.execute(
                    "INSERT INTO jobs (id, video_path, status, created_at, updated_at, tvdb_id) "
                    "VALUES ('old', 'Show {tvdb-555}/S01E01.mkv', 'completed', 0, 0, NULL)")
            store2 = JobStore(db_path)  # re-opening re-runs _migrate()
            self.assertEqual(store2.get("old")["tvdb_id"], 555)


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

    def test_update_rejects_unknown_column(self):
        # Real gap this closes (production-readiness audit, 2026-09-21):
        # update()'s column names are interpolated directly into the SQL
        # text -- values are parameterized, but nothing previously
        # stopped an unknown key from reaching the query at all.
        job = self.store.create("Show/S01E01.mkv", "tr")
        with self.assertRaises(JobStoreError):
            self.store.update(job["id"], not_a_real_column="value")

    def test_needs_review_defaults_to_zero(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        self.assertEqual(job["needs_review"], 0)

    def test_finish_persists_needs_review_count(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        claimed = self.store.claim()
        result = self.store.finish(claimed["id"], "completed", needs_review=2)
        self.assertEqual(result["needs_review"], 2)
        self.assertEqual(self.store.get(claimed["id"])["needs_review"], 2)

    def test_finish_without_needs_review_leaves_default(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        claimed = self.store.claim()
        result = self.store.finish(claimed["id"], "completed")
        self.assertEqual(result["needs_review"], 0)


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


class SeriesGroupingTests(JobStoreTestCase):
    """tvdb_id is extracted once at create() time (glossary_profile.
    find_tvdb_id(), already used elsewhere for the same purpose) and
    persisted, so series grouping never re-parses video_path per query."""

    def test_tvdb_id_extracted_from_path_on_create(self):
        job = self.store.create("Show (2020) {tvdb-383383}/Season 01/S01E01.mkv")
        self.assertEqual(job["tvdb_id"], 383383)

    def test_no_tvdb_tag_leaves_tvdb_id_none(self):
        job = self.store.create("Some Show/Season 01/S01E01.mkv")
        self.assertIsNone(job["tvdb_id"])

    def test_list_series_groups_by_tvdb_id(self):
        self.store.create("Show A {tvdb-111}/S01E01.mkv")
        self.store.create("Show A {tvdb-111}/S01E02.mkv")
        self.store.create("Show B {tvdb-222}/S01E01.mkv")
        series = {s["tvdb_id"]: s for s in self.store.list_series()}
        self.assertEqual(series[111]["total"], 2)
        self.assertEqual(series[222]["total"], 1)

    def test_list_series_buckets_untagged_jobs_under_none(self):
        self.store.create("Show A {tvdb-111}/S01E01.mkv")
        self.store.create("Untagged Show/S01E01.mkv")
        series = {s["tvdb_id"]: s for s in self.store.list_series()}
        self.assertIn(None, series)
        self.assertEqual(series[None]["total"], 1)

    def test_list_series_counts_split_by_status(self):
        j1 = self.store.create("Show A {tvdb-111}/S01E01.mkv")
        self.store.create("Show A {tvdb-111}/S01E02.mkv")
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "completed")
        series = {s["tvdb_id"]: s for s in self.store.list_series()}
        self.assertEqual(series[111]["counts"]["COMPLETED"], 1)
        self.assertEqual(series[111]["counts"]["QUEUED"], 1)

    def test_list_by_tvdb_id_returns_only_matching_jobs(self):
        self.store.create("Show A {tvdb-111}/S01E01.mkv")
        self.store.create("Show B {tvdb-222}/S01E01.mkv")
        jobs = self.store.list_by_tvdb_id(111)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["tvdb_id"], 111)

    def test_list_by_tvdb_id_none_returns_untagged_jobs(self):
        self.store.create("Show A {tvdb-111}/S01E01.mkv")
        self.store.create("Untagged Show/S01E01.mkv")
        jobs = self.store.list_by_tvdb_id(None)
        self.assertEqual(len(jobs), 1)
        self.assertIsNone(jobs[0]["tvdb_id"])

    def test_list_by_tvdb_id_orders_by_video_path(self):
        self.store.create("Show A {tvdb-111}/S01E02.mkv")
        self.store.create("Show A {tvdb-111}/S01E01.mkv")
        jobs = self.store.list_by_tvdb_id(111)
        self.assertEqual([j["video_path"] for j in jobs],
                         ["Show A {tvdb-111}/S01E01.mkv", "Show A {tvdb-111}/S01E02.mkv"])


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


class RetryOverwriteInheritanceTests(JobStoreTestCase):
    """Real bug (2026-09-19): a plain bool=False default on retry()'s
    overwrite_original/overwrite_english meant every retry submitted with
    no explicit overwrite choice (the GUI's one-click Retry button does
    exactly this) silently reset both flags, keeping stale pre-existing
    output instead of replacing it."""

    def test_retry_without_override_inherits_true_overwrite_original(self):
        self.store.create("Show/S01E01.mkv", "tr", overwrite_original=True)
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        retried = self.store.retry(claimed["id"])
        self.assertTrue(retried["overwrite_original"])

    def test_retry_without_override_inherits_false_overwrite_original(self):
        self.store.create("Show/S01E01.mkv", "tr", overwrite_original=False)
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        retried = self.store.retry(claimed["id"])
        self.assertFalse(retried["overwrite_original"])

    def test_retry_without_override_inherits_true_overwrite_english(self):
        self.store.create("Show/S01E01.mkv", "tr", overwrite_english=True)
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        retried = self.store.retry(claimed["id"])
        self.assertTrue(retried["overwrite_english"])

    def test_retry_without_override_inherits_false_overwrite_english(self):
        self.store.create("Show/S01E01.mkv", "tr", overwrite_english=False)
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        retried = self.store.retry(claimed["id"])
        self.assertFalse(retried["overwrite_english"])

    def test_explicit_override_still_wins_over_inheritance(self):
        self.store.create("Show/S01E01.mkv", "tr", overwrite_original=True, overwrite_english=True)
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        retried = self.store.retry(claimed["id"], overwrite_original=False, overwrite_english=False)
        self.assertFalse(retried["overwrite_original"])
        self.assertFalse(retried["overwrite_english"])


class OrphanRecoveryTests(JobStoreTestCase):
    def test_single_orphaned_running_job_becomes_queued(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        self.store.claim()
        recovered = self.store.recover_orphaned_jobs()
        self.assertEqual(recovered, [job["id"]])
        after = self.store.get(job["id"])
        self.assertEqual(after["status"], "queued")
        self.assertIsNone(after["started_at"])

    def test_multiple_orphaned_jobs_all_recovered(self):
        self.store.create("Show/S01E01.mkv", "tr")
        self.store.create("Show/S01E02.mkv", "tr")
        self.store.claim()
        self.store.claim()
        recovered = self.store.recover_orphaned_jobs()
        self.assertEqual(len(recovered), 2)
        statuses = {j["video_path"]: j["status"] for j in self.store.list()[0]}
        self.assertEqual(statuses["Show/S01E01.mkv"], "queued")
        self.assertEqual(statuses["Show/S01E02.mkv"], "queued")

    def test_queued_job_is_left_untouched(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        self.store.recover_orphaned_jobs()
        self.assertEqual(self.store.get(job["id"])["status"], "queued")

    def test_completed_job_is_left_untouched(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "completed")
        self.store.recover_orphaned_jobs()
        self.assertEqual(self.store.get(job["id"])["status"], "completed")

    def test_failed_job_is_left_untouched(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        self.store.recover_orphaned_jobs()
        self.assertEqual(self.store.get(job["id"])["status"], "failed")

    def test_cancelled_job_is_left_untouched(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        self.store.request_cancel(job["id"])
        self.store.recover_orphaned_jobs()
        self.assertEqual(self.store.get(job["id"])["status"], "cancelled")

    def test_recovery_is_idempotent(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        self.store.claim()
        first = self.store.recover_orphaned_jobs()
        second = self.store.recover_orphaned_jobs()
        self.assertEqual(first, [job["id"]])
        self.assertEqual(second, [])

    def test_recovered_job_can_be_claimed_and_finished_normally(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        self.store.claim()
        self.store.recover_orphaned_jobs()
        reclaimed = self.store.claim()
        self.assertEqual(reclaimed["id"], job["id"])
        self.assertEqual(reclaimed["status"], "running")
        finished = self.store.finish(reclaimed["id"], "completed")
        self.assertEqual(finished["status"], "completed")

    def test_recovery_is_job_type_agnostic(self):
        # A stuck SRT-translation job must be recovered exactly like a
        # stuck video job -- recover_orphaned_jobs() has no job_type
        # filter, by construction.
        job = self.store.create_srt_translation("in/ep.tr.srt", "out/ep.en.srt")
        self.store.claim()
        recovered = self.store.recover_orphaned_jobs()
        self.assertEqual(recovered, [job["id"]])
        self.assertEqual(self.store.get(job["id"])["status"], "queued")


class SrtTranslationJobCreationTests(JobStoreTestCase):
    def test_create_returns_queued_srt_translation_job(self):
        job = self.store.create_srt_translation("in/ep.tr.srt", "out/ep.en.srt")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["job_type"], "srt_translation")
        self.assertEqual(job["source_srt_path"], "in/ep.tr.srt")
        self.assertEqual(job["destination_srt_path"], "out/ep.en.srt")
        self.assertEqual(job["source_lang"], "auto")
        self.assertEqual(job["target_lang"], "en")
        self.assertFalse(job["overwrite_english"])

    def test_video_jobs_default_to_job_type_video(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        self.assertEqual(job["job_type"], "video")

    def test_optional_video_association_derives_tvdb_id(self):
        job = self.store.create_srt_translation(
            "in/ep.tr.srt", "out/ep.en.srt",
            video_path="Show (2020) {tvdb-383383}/Season 01/S01E01.mkv")
        self.assertEqual(job["tvdb_id"], 383383)
        self.assertEqual(job["video_path"], "Show (2020) {tvdb-383383}/Season 01/S01E01.mkv")

    def test_no_video_association_leaves_tvdb_id_none(self):
        job = self.store.create_srt_translation("in/ep.tr.srt", "out/ep.en.srt")
        self.assertIsNone(job["tvdb_id"])
        self.assertEqual(job["video_path"], "")

    def test_duplicate_destination_while_active_is_refused(self):
        self.store.create_srt_translation("in/ep.tr.srt", "out/ep.en.srt")
        with self.assertRaises(JobStoreError):
            self.store.create_srt_translation("in/other.tr.srt", "out/ep.en.srt")

    def test_same_destination_allowed_again_once_terminal(self):
        job = self.store.create_srt_translation("in/ep.tr.srt", "out/ep.en.srt")
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        second = self.store.create_srt_translation("in/ep.tr.srt", "out/ep.en.srt")
        self.assertNotEqual(second["id"], job["id"])

    def test_video_job_and_srt_translation_job_do_not_collide_on_video_path(self):
        # Real scoping requirement: create()'s duplicate check must be
        # job_type='video'-scoped so an unrelated srt_translation job
        # referencing the same episode never trips it, and vice versa.
        self.store.create_srt_translation(
            "in/ep.tr.srt", "out/ep.en.srt", video_path="Show/S01E01.mkv")
        self.store.create("Show/S01E01.mkv", "tr")  # must not raise

    def test_claim_and_finish_work_for_srt_translation_jobs(self):
        job = self.store.create_srt_translation("in/ep.tr.srt", "out/ep.en.srt")
        claimed = self.store.claim()
        self.assertEqual(claimed["id"], job["id"])
        self.assertEqual(claimed["status"], "running")
        finished = self.store.finish(claimed["id"], "completed", outputs=["out/ep.en.srt"])
        self.assertEqual(finished["status"], "completed")

    def test_retry_stays_an_srt_translation_job_with_same_paths(self):
        job = self.store.create_srt_translation(
            "in/ep.tr.srt", "out/ep.en.srt", overwrite_english=True)
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        retried = self.store.retry(claimed["id"])
        self.assertEqual(retried["job_type"], "srt_translation")
        self.assertEqual(retried["source_srt_path"], "in/ep.tr.srt")
        self.assertEqual(retried["destination_srt_path"], "out/ep.en.srt")
        self.assertTrue(retried["overwrite_english"])
        self.assertEqual(retried["retry_of_job_id"], claimed["id"])
        self.assertEqual(retried["attempt"], 2)

    def test_retry_of_legacy_non_english_srt_job_targets_english(self):
        # Rows created before the English-only restriction could record
        # another target and a `.fr.srt` destination.
        job = self.store.create_srt_translation("in/ep.tr.srt", "out/ep.fr.srt", target_lang="fr")
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        retried = self.store.retry(claimed["id"])
        self.assertEqual(retried["target_lang"], "en")
        self.assertEqual(retried["destination_srt_path"], "out/ep.en.srt")

    def test_retry_of_legacy_non_english_video_job_targets_english(self):
        job = self.store.create("Show/S01E01.mkv", "auto", target_lang="fr")
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        self.assertEqual(self.store.retry(claimed["id"])["target_lang"], "en")

    def test_retry_of_srt_translation_job_inherits_overwrite_english(self):
        job = self.store.create_srt_translation(
            "in/ep.tr.srt", "out/ep.en.srt", overwrite_english=False)
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        retried = self.store.retry(claimed["id"])
        self.assertFalse(retried["overwrite_english"])

    def test_source_is_uploaded_defaults_to_false(self):
        job = self.store.create_srt_translation("in/ep.tr.srt", "out/ep.en.srt")
        self.assertFalse(job["source_is_uploaded"])

    def test_source_is_uploaded_persists_when_true(self):
        job = self.store.create_srt_translation(
            "abc123.srt", "out/ep.en.srt", source_is_uploaded=True)
        self.assertTrue(job["source_is_uploaded"])
        self.assertTrue(self.store.get(job["id"])["source_is_uploaded"])

    def test_retry_of_uploaded_source_job_inherits_source_is_uploaded(self):
        job = self.store.create_srt_translation(
            "abc123.srt", "out/ep.en.srt", source_is_uploaded=True)
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        retried = self.store.retry(claimed["id"])
        self.assertTrue(retried["source_is_uploaded"])
        self.assertEqual(retried["source_srt_path"], "abc123.srt")

    def test_retry_of_library_source_job_keeps_source_is_uploaded_false(self):
        job = self.store.create_srt_translation("in/ep.tr.srt", "out/ep.en.srt")
        claimed = self.store.claim()
        self.store.finish(claimed["id"], "failed")
        retried = self.store.retry(claimed["id"])
        self.assertFalse(retried["source_is_uploaded"])
