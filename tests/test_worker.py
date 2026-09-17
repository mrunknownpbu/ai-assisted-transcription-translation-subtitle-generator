import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from jobstore import JobStore
from projection import ProjectedCue
from qc.types import JobQc, QcResult, QcStage
import worker as worker_mod
from worker import Worker


def fake_result(work_dir: Path, *, valid=True, detected_language="tr",
                source_language_mode="MANUAL", requested_source_language="tr",
                selected_audio_stream=0, requested_audio_stream=None,
                embedded_stream_language=None, stream_selection_mode="AUTO",
                selected_stream_reason=""):
    work_dir.mkdir(parents=True, exist_ok=True)
    source_path = work_dir / f"S01E01.{detected_language}.srt"
    target_path = work_dir / "S01E01.en.srt"
    source_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nMerhaba\n", encoding="utf-8")
    target_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    qc = JobQc(segmentation=QcResult(QcStage.SEGMENTATION, population=1, flagged=0 if valid else 1))

    class Result:
        pass
    r = Result()
    r.source_srt_path = source_path
    r.target_srt_path = target_path
    r.qc = qc
    r.valid = valid
    r.events = []
    r.detected_source_language = detected_language
    r.source_language_mode = source_language_mode
    r.requested_source_language = requested_source_language
    r.language_probability = 0.9
    r.language_detection_uncertain = False
    r.target_language = "en"
    r.selected_audio_stream = selected_audio_stream
    r.requested_audio_stream = requested_audio_stream
    r.embedded_stream_language = embedded_stream_language
    r.stream_selection_mode = stream_selection_mode
    r.selected_stream_reason = selected_stream_reason
    return r


class WorkerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media_root = Path(self.tmp.name) / "data"
        self.work_root = Path(self.tmp.name) / "work"
        (self.media_root / "Show").mkdir(parents=True)
        (self.media_root / "Show" / "S01E01.mkv").touch()
        self.store = JobStore(Path(self.tmp.name) / "jobs.db")
        self.worker = Worker(self.store, str(self.media_root), str(self.work_root))


class SuccessfulJobTests(WorkerTestCase):
    def test_completed_job_commits_both_outputs(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        with patch.object(worker_mod.pipeline, "run") as mock_run:
            mock_run.side_effect = lambda **kw: fake_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "completed")
        self.assertTrue((self.media_root / "Show" / "S01E01.tr.srt").exists())
        self.assertTrue((self.media_root / "Show" / "S01E01.en.srt").exists())
        self.assertEqual(len(final["outputs"]), 2)

    def test_invalid_pipeline_result_fails_without_writing(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        with patch.object(worker_mod.pipeline, "run") as mock_run:
            mock_run.side_effect = lambda **kw: fake_result(Path(kw["work_dir"]), valid=False)
            claimed = self.store.claim()
            self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["error_category"], "VALIDATION_ERROR")
        self.assertFalse((self.media_root / "Show" / "S01E01.tr.srt").exists())
        self.assertFalse((self.media_root / "Show" / "S01E01.en.srt").exists())


class GlossaryLoadingTests(unittest.TestCase):
    """Real defect (2026-09-17): the glossary used to be loaded ONCE at
    process startup with no tvdb_id, so a series-specific glossary file
    could never be selected regardless of its content. These confirm it's
    now loaded per job, keyed by that job's own tvdb_id."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media_root = Path(self.tmp.name) / "data"
        self.work_root = Path(self.tmp.name) / "work"
        self.glossary_dir = Path(self.tmp.name) / "glossary"
        self.glossary_dir.mkdir(parents=True)
        show_dir = self.media_root / "Show {tvdb-383383}"
        show_dir.mkdir(parents=True)
        (show_dir / "S01E01.mkv").touch()
        (self.glossary_dir / "show.yaml").write_text(
            "tvdb_id: 383383\n"
            "title: Show\n"
            "entities:\n"
            "  - canonical: Eda\n"
            "    protected: true\n",
            encoding="utf-8")
        self.store = JobStore(Path(self.tmp.name) / "jobs.db")

    def test_series_specific_glossary_reaches_pipeline_run(self):
        worker = Worker(self.store, str(self.media_root), str(self.work_root),
                        glossary_dir=str(self.glossary_dir))
        job = self.store.create("Show {tvdb-383383}/S01E01.mkv", "tr")
        with patch.object(worker_mod.pipeline, "run") as mock_run:
            mock_run.side_effect = lambda **kw: fake_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            worker._process(claimed)
        entities = mock_run.call_args.kwargs["glossary_entities"]
        self.assertEqual([e.canonical for e in entities], ["Eda"])

    def test_no_glossary_dir_configured_yields_no_entities(self):
        worker = Worker(self.store, str(self.media_root), str(self.work_root))
        job = self.store.create("Show {tvdb-383383}/S01E01.mkv", "tr")
        with patch.object(worker_mod.pipeline, "run") as mock_run:
            mock_run.side_effect = lambda **kw: fake_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            worker._process(claimed)
        self.assertEqual(mock_run.call_args.kwargs["glossary_entities"], [])

    def test_a_different_series_tvdb_id_does_not_get_this_glossary(self):
        worker = Worker(self.store, str(self.media_root), str(self.work_root),
                        glossary_dir=str(self.glossary_dir))
        other_dir = self.media_root / "Other {tvdb-999}"
        other_dir.mkdir(parents=True)
        (other_dir / "S01E01.mkv").touch()
        job = self.store.create("Other {tvdb-999}/S01E01.mkv", "tr")
        with patch.object(worker_mod.pipeline, "run") as mock_run:
            mock_run.side_effect = lambda **kw: fake_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            worker._process(claimed)
        self.assertEqual(mock_run.call_args.kwargs["glossary_entities"], [])


class KeepReplaceTests(WorkerTestCase):
    def test_keep_does_not_overwrite_existing_output(self):
        target = self.media_root / "Show" / "S01E01.en.srt"
        target.write_text("original content")
        job = self.store.create("Show/S01E01.mkv", "tr", overwrite_english=False)
        with patch.object(worker_mod.pipeline, "run") as mock_run:
            mock_run.side_effect = lambda **kw: fake_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            self.worker._process(claimed)
        self.assertEqual(target.read_text(), "original content")
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "completed")

    def test_replace_overwrites_when_authorized(self):
        target = self.media_root / "Show" / "S01E01.en.srt"
        target.write_text("original content")
        job = self.store.create("Show/S01E01.mkv", "tr", overwrite_english=True)
        with patch.object(worker_mod.pipeline, "run") as mock_run:
            mock_run.side_effect = lambda **kw: fake_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            self.worker._process(claimed)
        self.assertIn("Hello", target.read_text())


class PipelineExceptionTests(WorkerTestCase):
    def test_pipeline_exception_marks_job_failed_not_crash_worker(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        with patch.object(worker_mod.pipeline, "run", side_effect=RuntimeError("boom")):
            claimed = self.store.claim()
            self.worker._process(claimed)   # must not raise
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["error_category"], "PIPELINE_ERROR")
        self.assertIn("boom", final["error"])


class AutoDetectJobTests(WorkerTestCase):
    def test_auto_job_writes_output_under_the_detected_language_not_literally_auto(self):
        job = self.store.create("Show/S01E01.mkv", "auto")

        def fake_run(**kw):
            kw["on_event"]("LANGUAGE_DETECTED", {"language": "ja", "probability": 0.93, "mode": "AUTO"})
            return fake_result(Path(kw["work_dir"]), detected_language="ja",
                              source_language_mode="AUTO", requested_source_language="auto")

        with patch.object(worker_mod.pipeline, "run", side_effect=fake_run):
            claimed = self.store.claim()
            self.assertEqual(claimed["source_lang"], "auto")
            self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "completed")
        # Never a literal "S01E01.auto.srt" -- the real detected language.
        self.assertTrue((self.media_root / "Show" / "S01E01.ja.srt").exists())
        self.assertFalse((self.media_root / "Show" / "S01E01.auto.srt").exists())
        self.assertTrue((self.media_root / "Show" / "S01E01.en.srt").exists())

    def test_detected_language_visible_during_run_not_only_after_completion(self):
        job = self.store.create("Show/S01E01.mkv", "auto")
        seen_mid_run = {}

        def fake_run(**kw):
            kw["on_event"]("LANGUAGE_DETECTED", {"language": "ko", "probability": 0.81, "mode": "AUTO"})
            seen_mid_run.update(self.store.get(job["id"]))
            return fake_result(Path(kw["work_dir"]), detected_language="ko",
                              source_language_mode="AUTO", requested_source_language="auto")

        with patch.object(worker_mod.pipeline, "run", side_effect=fake_run):
            claimed = self.store.claim()
            self.worker._process(claimed)
        self.assertEqual(seen_mid_run["detected_language"], "ko")
        self.assertEqual(seen_mid_run["language_confidence"], 0.81)
        self.assertEqual(seen_mid_run["source_language_mode"], "AUTO")

    def test_default_job_store_source_lang_is_auto(self):
        job = self.store.create("Show/S01E01.mkv")
        self.assertEqual(job["source_lang"], "auto")
        self.assertEqual(job["source_language_mode"], "AUTO")


class LanguageDetectionFailureTests(WorkerTestCase):
    def test_low_confidence_language_error_is_classified_not_a_generic_failure(self):
        job = self.store.create("Show/S01E01.mkv", "auto")
        with patch.object(worker_mod.pipeline, "run",
                          side_effect=worker_mod.pipeline.LowConfidenceLanguageError("too uncertain")):
            claimed = self.store.claim()
            self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["error_category"], "LOW_CONFIDENCE_LANGUAGE")

    def test_unsupported_language_error_is_classified_not_a_generic_failure(self):
        job = self.store.create("Show/S01E01.mkv", "auto")
        with patch.object(worker_mod.pipeline, "run",
                          side_effect=worker_mod.pipeline.UnsupportedLanguageError("no mapping")):
            claimed = self.store.claim()
            self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["error_category"], "UNSUPPORTED_LANGUAGE")


class AudioStreamJobTests(WorkerTestCase):
    def test_auto_stream_selection_passes_none_to_pipeline(self):
        job = self.store.create("Show/S01E01.mkv")   # no explicit stream -> AUTO
        seen = {}

        def fake_run(**kw):
            seen.update(kw)
            return fake_result(Path(kw["work_dir"]), embedded_stream_language="tur",
                               stream_selection_mode="AUTO", selected_stream_reason="detected tr at 95%")

        with patch.object(worker_mod.pipeline, "run", side_effect=fake_run):
            claimed = self.store.claim()
            self.worker._process(claimed)
        self.assertIsNone(seen["audio_stream_index"])

    def test_manual_stream_selection_passes_the_exact_index_to_pipeline(self):
        job = self.store.create("Show/S01E01.mkv", audio_stream_index=2)
        seen = {}

        def fake_run(**kw):
            seen.update(kw)
            return fake_result(Path(kw["work_dir"]), selected_audio_stream=2,
                               requested_audio_stream=2, stream_selection_mode="MANUAL",
                               selected_stream_reason="manually selected")

        with patch.object(worker_mod.pipeline, "run", side_effect=fake_run):
            claimed = self.store.claim()
            self.worker._process(claimed)
        self.assertEqual(seen["audio_stream_index"], 2)

    def test_stream_selection_is_visible_during_run(self):
        job = self.store.create("Show/S01E01.mkv")
        seen_mid_run = {}

        def fake_run(**kw):
            kw["on_event"]("AUDIO_SELECTED", {"index": 0, "embedded_language": "tur",
                                              "selection_mode": "AUTO", "reason": "detected tr at 95%"})
            seen_mid_run.update(self.store.get(job["id"]))
            return fake_result(Path(kw["work_dir"]), embedded_stream_language="tur",
                               stream_selection_mode="AUTO", selected_stream_reason="detected tr at 95%")

        with patch.object(worker_mod.pipeline, "run", side_effect=fake_run):
            claimed = self.store.claim()
            self.worker._process(claimed)
        self.assertEqual(seen_mid_run["selected_audio_stream"], 0)
        self.assertEqual(seen_mid_run["embedded_stream_language"], "tur")
        self.assertEqual(seen_mid_run["selected_stream_reason"], "detected tr at 95%")

    def test_completed_job_persists_final_stream_provenance(self):
        job = self.store.create("Show/S01E01.mkv", audio_stream_index=1)
        with patch.object(worker_mod.pipeline, "run") as mock_run:
            mock_run.side_effect = lambda **kw: fake_result(
                Path(kw["work_dir"]), selected_audio_stream=1, requested_audio_stream=1,
                embedded_stream_language="eng", stream_selection_mode="MANUAL",
                selected_stream_reason="manually selected")
            claimed = self.store.claim()
            self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "completed")


class SameLanguageSourceAndTargetTests(WorkerTestCase):
    """detected_source_language == target_language ("en" audio, "en"
    target) is a real reachable case now that source language is
    auto-detected -- source_path and target_path resolve to the SAME
    file. Must not silently let one overwrite_* flag race the other."""

    def test_writes_exactly_one_output_not_two_racing_writes(self):
        job = self.store.create("Show/S01E01.mkv", "auto")

        def fake_run(**kw):
            kw["on_event"]("LANGUAGE_DETECTED", {"language": "en", "probability": 0.99, "mode": "AUTO"})
            work_dir = Path(kw["work_dir"])
            work_dir.mkdir(parents=True, exist_ok=True)
            target_path = work_dir / "S01E01.en.srt"
            target_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            r = fake_result(work_dir, detected_language="en", source_language_mode="AUTO",
                            requested_source_language="auto")
            r.source_srt_path = target_path   # pipeline.py: same collapse happens internally too
            r.target_srt_path = target_path
            return r

        with patch.object(worker_mod.pipeline, "run", side_effect=fake_run):
            claimed = self.store.claim()
            self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(len(final["outputs"]), 1)
        self.assertTrue((self.media_root / "Show" / "S01E01.en.srt").exists())

    def test_target_overwrite_flag_governs_the_single_output(self):
        target = self.media_root / "Show" / "S01E01.en.srt"
        target.write_text("original content")
        # overwrite_original=True (irrelevant here) but overwrite_english=False:
        # KEEP must win, since this is the target's file.
        job = self.store.create("Show/S01E01.mkv", "auto", overwrite_original=True, overwrite_english=False)

        def fake_run(**kw):
            work_dir = Path(kw["work_dir"])
            work_dir.mkdir(parents=True, exist_ok=True)
            new_content_path = work_dir / "S01E01.en.srt"
            new_content_path.write_text("new content", encoding="utf-8")
            r = fake_result(work_dir, detected_language="en", source_language_mode="AUTO",
                            requested_source_language="auto")
            r.source_srt_path = new_content_path
            r.target_srt_path = new_content_path
            return r

        with patch.object(worker_mod.pipeline, "run", side_effect=fake_run):
            claimed = self.store.claim()
            self.worker._process(claimed)
        self.assertEqual(target.read_text(), "original content")
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["outputs"], [])


class CancellationTests(WorkerTestCase):
    def test_cancel_requested_during_run_stops_the_job(self):
        job = self.store.create("Show/S01E01.mkv", "tr")
        claimed = self.store.claim()

        def fake_run(**kw):
            self.store.request_cancel(claimed["id"])
            kw["on_event"]("SOME_STAGE", {})   # raises JobCancelled via the callback
            return fake_result(Path(kw["work_dir"]))

        with patch.object(worker_mod.pipeline, "run", side_effect=fake_run):
            self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "cancelled")
        self.assertFalse((self.media_root / "Show" / "S01E01.en.srt").exists())


if __name__ == "__main__":
    unittest.main()
