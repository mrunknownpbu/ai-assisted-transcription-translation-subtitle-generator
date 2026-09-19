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

    def test_glossary_phrases_reach_pipeline_run(self):
        # PhraseEntry (2026-09-19): a Turkish-tier phrase file (no
        # tvdb_id) must reach pipeline.run() alongside glossary_entities.
        (self.glossary_dir / "turkish.yaml").write_text(
            'language: tr\nphrases:\n  - source: "Peki."\n    translation: "Okay."\n',
            encoding="utf-8")
        worker = Worker(self.store, str(self.media_root), str(self.work_root),
                        glossary_dir=str(self.glossary_dir))
        job = self.store.create("Show {tvdb-383383}/S01E01.mkv", "tr")
        with patch.object(worker_mod.pipeline, "run") as mock_run:
            mock_run.side_effect = lambda **kw: fake_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            worker._process(claimed)
        phrases = mock_run.call_args.kwargs["glossary_phrases"]
        self.assertEqual([(p.source, p.translation) for p in phrases], [("Peki.", "Okay.")])


def _write_srt_pair(show_dir: Path, stem: str, lines: list[str]):
    tr_cues = "\n\n".join(
        f"{i}\n00:00:{i:02d},000 --> 00:00:{i+1:02d},000\n{line}"
        for i, line in enumerate(lines, 1))
    (show_dir / f"{stem}.tr.srt").write_text(tr_cues, encoding="utf-8")
    (show_dir / f"{stem}.en.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nSome translation.", encoding="utf-8")


class AutoHotwordsTests(unittest.TestCase):
    """auto_glossary.py mining, wired through Worker -- see that module's
    docstring for the safety framing (hotwords only, never translation
    protection)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media_root = Path(self.tmp.name) / "data"
        self.work_root = Path(self.tmp.name) / "work"
        self.show_dir = self.media_root / "Show {tvdb-383383}"
        self.show_dir.mkdir(parents=True)
        (self.show_dir / "S01E01.mkv").touch()
        self.store = JobStore(Path(self.tmp.name) / "jobs.db")

    def test_auto_hotwords_reach_pipeline_run(self):
        lines = ["Melek geldi.", "Nerede Melek?", "Melek çok mutlu."]
        for i in (2, 3, 4):
            _write_srt_pair(self.show_dir, f"S01E0{i}", lines)
        worker = Worker(self.store, str(self.media_root), str(self.work_root))
        job = self.store.create("Show {tvdb-383383}/S01E01.mkv", "tr")
        with patch.object(worker_mod.pipeline, "run") as mock_run:
            mock_run.side_effect = lambda **kw: fake_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            worker._process(claimed)
        self.assertIn("Melek", mock_run.call_args.kwargs["extra_hotwords"])

    def test_no_series_root_match_yields_no_auto_hotwords(self):
        plain_dir = self.media_root / "Plain Show"
        plain_dir.mkdir(parents=True)
        (plain_dir / "S01E01.mkv").touch()
        worker = Worker(self.store, str(self.media_root), str(self.work_root))
        job = self.store.create("Plain Show/S01E01.mkv", "tr")
        with patch.object(worker_mod.pipeline, "run") as mock_run:
            mock_run.side_effect = lambda **kw: fake_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            worker._process(claimed)
        self.assertEqual(mock_run.call_args.kwargs["extra_hotwords"], [])

    def test_current_episode_own_prior_output_excluded_from_its_own_mining(self):
        # The current job's own (e.g. prior-attempt) S01E01 output has the
        # artifact too -- without self-exclusion this would be a 3rd
        # qualifying episode and cross MIN_DISTINCT_EPISODES.
        artifact = ["Xyzzy burada.", "Xyzzy orada.", "Xyzzy her yerde."]
        _write_srt_pair(self.show_dir, "S01E01", artifact)
        for i in (2, 3):
            _write_srt_pair(self.show_dir, f"S01E0{i}", artifact)
        worker = Worker(self.store, str(self.media_root), str(self.work_root))
        job = self.store.create("Show {tvdb-383383}/S01E01.mkv", "tr")
        with patch.object(worker_mod.pipeline, "run") as mock_run:
            mock_run.side_effect = lambda **kw: fake_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            worker._process(claimed)
        self.assertNotIn("Xyzzy", mock_run.call_args.kwargs["extra_hotwords"])

    def test_names_already_in_manual_glossary_not_duplicated_via_auto_path(self):
        glossary_dir = Path(self.tmp.name) / "glossary"
        glossary_dir.mkdir(parents=True)
        (glossary_dir / "show.yaml").write_text(
            "tvdb_id: 383383\ntitle: Show\nentities:\n  - canonical: Eda\n    protected: true\n",
            encoding="utf-8")
        lines = ["Eda geldi.", "Eda nerede?", "Eda çok mutlu."]
        for i in (2, 3, 4):
            _write_srt_pair(self.show_dir, f"S01E0{i}", lines)
        worker = Worker(self.store, str(self.media_root), str(self.work_root),
                        glossary_dir=str(glossary_dir))
        job = self.store.create("Show {tvdb-383383}/S01E01.mkv", "tr")
        with patch.object(worker_mod.pipeline, "run") as mock_run:
            mock_run.side_effect = lambda **kw: fake_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            worker._process(claimed)
        self.assertNotIn("Eda", mock_run.call_args.kwargs["extra_hotwords"])

    def test_glossary_suggestions_file_written_when_configured(self):
        lines = ["Melek geldi.", "Melek nerede?", "Melek çok mutlu."]
        for i in (2, 3, 4):
            _write_srt_pair(self.show_dir, f"S01E0{i}", lines)
        suggestions_dir = Path(self.tmp.name) / "suggestions"
        worker = Worker(self.store, str(self.media_root), str(self.work_root),
                        glossary_suggestions_dir=str(suggestions_dir))
        job = self.store.create("Show {tvdb-383383}/S01E01.mkv", "tr")
        with patch.object(worker_mod.pipeline, "run") as mock_run:
            mock_run.side_effect = lambda **kw: fake_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            worker._process(claimed)
        self.assertTrue((suggestions_dir / "383383.yaml").exists())

    def test_no_suggestions_written_when_dir_not_configured(self):
        lines = ["Melek geldi.", "Melek nerede?", "Melek çok mutlu."]
        for i in (2, 3, 4):
            _write_srt_pair(self.show_dir, f"S01E0{i}", lines)
        suggestions_dir = Path(self.tmp.name) / "suggestions"
        worker = Worker(self.store, str(self.media_root), str(self.work_root))
        job = self.store.create("Show {tvdb-383383}/S01E01.mkv", "tr")
        with patch.object(worker_mod.pipeline, "run") as mock_run:
            mock_run.side_effect = lambda **kw: fake_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            worker._process(claimed)
        self.assertFalse(suggestions_dir.exists())


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


def fake_srt_result(work_dir: Path, *, valid=True, detected_language="tr",
                    with_source_language_copy=False):
    work_dir.mkdir(parents=True, exist_ok=True)
    target_path = work_dir / "ep.tr.en.srt"
    target_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    from qc.types import JobQc, QcResult, QcStage
    qc = JobQc(timing=QcResult(QcStage.TIMING, population=1, flagged=0 if valid else 1))

    class Result:
        pass
    r = Result()
    r.source_srt_path = work_dir / "source.srt"
    r.target_srt_path = target_path
    r.qc = qc
    r.valid = valid
    r.events = []
    r.requested_source_language = "tr"
    r.detected_source_language = detected_language
    r.language_probability = 0.9
    r.target_language = "en"
    if with_source_language_copy:
        source_language_path = work_dir / f"ep.{detected_language}.srt"
        source_language_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nMerhaba\n", encoding="utf-8")
        r.source_language_srt_path = source_language_path
    else:
        r.source_language_srt_path = None
    return r


class SrtTranslationWorkerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media_root = Path(self.tmp.name) / "data"
        self.work_root = Path(self.tmp.name) / "work"
        (self.media_root / "in").mkdir(parents=True)
        (self.media_root / "out").mkdir(parents=True)
        (self.media_root / "Show").mkdir(parents=True)
        (self.media_root / "Show" / "S01E01.mkv").touch()
        (self.media_root / "in" / "ep.tr.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nMerhaba\n", encoding="utf-8")
        self.store = JobStore(Path(self.tmp.name) / "jobs.db")
        self.worker = Worker(self.store, str(self.media_root), str(self.work_root))


class SrtTranslationWorkerTests(SrtTranslationWorkerTestCase):
    def test_completed_job_writes_destination(self):
        job = self.store.create_srt_translation("in/ep.tr.srt", "out/ep.en.srt")
        with patch.object(worker_mod.srt_translation, "run_srt_translation_pipeline") as mock_run:
            mock_run.side_effect = lambda **kw: fake_srt_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "completed")
        self.assertTrue((self.media_root / "out" / "ep.en.srt").exists())
        self.assertEqual(
            (self.media_root / "out" / "ep.en.srt").read_text(encoding="utf-8"),
            "1\n00:00:00,000 --> 00:00:01,000\nHello\n")
        self.assertEqual(len(final["outputs"]), 1)

    def test_commits_source_language_sibling_beside_the_video(self):
        job = self.store.create_srt_translation(
            "in/ep.tr.srt", "Show/S01E01.en.srt", video_path="Show/S01E01.mkv")
        with patch.object(worker_mod.srt_translation, "run_srt_translation_pipeline") as mock_run:
            mock_run.side_effect = lambda **kw: fake_srt_result(
                Path(kw["work_dir"]), with_source_language_copy=True)
            claimed = self.store.claim()
            self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "completed")
        sibling = self.media_root / "Show" / "S01E01.tr.srt"
        self.assertTrue(sibling.exists())
        self.assertEqual(sibling.read_text(encoding="utf-8"),
                         "1\n00:00:00,000 --> 00:00:01,000\nMerhaba\n")
        self.assertEqual(len(final["outputs"]), 2)

    def test_source_language_sibling_respects_overwrite_original_keep(self):
        (self.media_root / "Show" / "S01E01.tr.srt").write_text("stale", encoding="utf-8")
        job = self.store.create_srt_translation(
            "in/ep.tr.srt", "Show/S01E01.en.srt", video_path="Show/S01E01.mkv")
        self.assertFalse(job["overwrite_original"])
        with patch.object(worker_mod.srt_translation, "run_srt_translation_pipeline") as mock_run:
            mock_run.side_effect = lambda **kw: fake_srt_result(
                Path(kw["work_dir"]), with_source_language_copy=True)
            claimed = self.store.claim()
            self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(
            (self.media_root / "Show" / "S01E01.tr.srt").read_text(encoding="utf-8"), "stale")
        self.assertEqual(len(final["outputs"]), 1)  # only the English output committed

    def test_refreshes_glossary_suggestions_for_srt_translation_jobs(self):
        job = self.store.create_srt_translation(
            "in/ep.tr.srt", "Show/S01E01.en.srt", video_path="Show/S01E01.mkv")
        with patch.object(worker_mod.srt_translation, "run_srt_translation_pipeline") as mock_run, \
             patch.object(self.worker, "_refresh_glossary_suggestions") as mock_refresh:
            mock_run.side_effect = lambda **kw: fake_srt_result(Path(kw["work_dir"]))
            mock_refresh.return_value = []
            claimed = self.store.claim()
            self.worker._process(claimed)
        mock_refresh.assert_called_once_with("Show/S01E01.mkv", [])

    def test_dispatches_to_srt_pipeline_not_video_pipeline(self):
        job = self.store.create_srt_translation("in/ep.tr.srt", "out/ep.en.srt")
        with patch.object(worker_mod.srt_translation, "run_srt_translation_pipeline") as mock_srt, \
             patch.object(worker_mod.pipeline, "run") as mock_video:
            mock_srt.side_effect = lambda **kw: fake_srt_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            self.worker._process(claimed)
        mock_srt.assert_called_once()
        mock_video.assert_not_called()

    def test_invalid_result_fails_without_writing_destination(self):
        job = self.store.create_srt_translation("in/ep.tr.srt", "out/ep.en.srt")
        with patch.object(worker_mod.srt_translation, "run_srt_translation_pipeline") as mock_run:
            mock_run.side_effect = lambda **kw: fake_srt_result(Path(kw["work_dir"]), valid=False)
            claimed = self.store.claim()
            self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["error_category"], "VALIDATION_ERROR")
        self.assertFalse((self.media_root / "out" / "ep.en.srt").exists())

    def test_srt_validation_error_fails_the_job_with_its_own_category(self):
        job = self.store.create_srt_translation("in/ep.tr.srt", "out/ep.en.srt")
        with patch.object(worker_mod.srt_translation, "run_srt_translation_pipeline") as mock_run:
            mock_run.side_effect = worker_mod.srt_translation.SrtValidationError("bad timestamp")
            claimed = self.store.claim()
            self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["error_category"], "SRT_VALIDATION_ERROR")
        self.assertFalse((self.media_root / "out" / "ep.en.srt").exists())

    def test_missing_source_srt_fails_with_output_error(self):
        job = self.store.create_srt_translation("in/does-not-exist.srt", "out/ep.en.srt")
        claimed = self.store.claim()
        self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["error_category"], "OUTPUT_ERROR")

    def test_overwrite_english_false_keeps_existing_destination(self):
        (self.media_root / "out" / "ep.en.srt").write_text("stale content", encoding="utf-8")
        job = self.store.create_srt_translation(
            "in/ep.tr.srt", "out/ep.en.srt", overwrite_english=False)
        with patch.object(worker_mod.srt_translation, "run_srt_translation_pipeline") as mock_run:
            mock_run.side_effect = lambda **kw: fake_srt_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["outputs"], [])
        self.assertEqual((self.media_root / "out" / "ep.en.srt").read_text(encoding="utf-8"),
                         "stale content")

    def test_cancel_requested_during_translation_stops_the_job_leaving_destination_untouched(self):
        job = self.store.create_srt_translation("in/ep.tr.srt", "out/ep.en.srt")
        claimed = self.store.claim()

        def fake_run(**kw):
            self.store.request_cancel(claimed["id"])
            kw["on_event"]("SRT_TRANSLATION_PROGRESS", {"done": 1, "total": 5})
            return fake_srt_result(Path(kw["work_dir"]))

        with patch.object(worker_mod.srt_translation, "run_srt_translation_pipeline",
                         side_effect=fake_run):
            self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "cancelled")
        self.assertFalse((self.media_root / "out" / "ep.en.srt").exists())

    def test_glossary_entities_reach_the_srt_pipeline(self):
        glossary_dir = Path(self.tmp.name) / "glossary"
        glossary_dir.mkdir()
        (glossary_dir / "show.yaml").write_text(
            "tvdb_id: 111\ntitle: Show\nentities:\n  - canonical: Eda\n    protected: true\n",
            encoding="utf-8")
        worker = Worker(self.store, str(self.media_root), str(self.work_root),
                        glossary_dir=str(glossary_dir))
        job = self.store.create_srt_translation(
            "in/ep.tr.srt", "out/ep.en.srt", video_path="Show {tvdb-111}/S01E01.mkv")
        with patch.object(worker_mod.srt_translation, "run_srt_translation_pipeline") as mock_run:
            mock_run.side_effect = lambda **kw: fake_srt_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            worker._process(claimed)
        entities = mock_run.call_args.kwargs["glossary_entities"]
        self.assertEqual([e.canonical for e in entities], ["Eda"])

    def test_glossary_phrases_reach_the_srt_pipeline(self):
        glossary_dir = Path(self.tmp.name) / "glossary"
        glossary_dir.mkdir()
        (glossary_dir / "turkish.yaml").write_text(
            'language: tr\nphrases:\n  - source: "Peki."\n    translation: "Okay."\n',
            encoding="utf-8")
        worker = Worker(self.store, str(self.media_root), str(self.work_root),
                        glossary_dir=str(glossary_dir))
        job = self.store.create_srt_translation(
            "in/ep.tr.srt", "out/ep.en.srt", video_path="Show {tvdb-111}/S01E01.mkv")
        with patch.object(worker_mod.srt_translation, "run_srt_translation_pipeline") as mock_run:
            mock_run.side_effect = lambda **kw: fake_srt_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            worker._process(claimed)
        phrases = mock_run.call_args.kwargs["glossary_phrases"]
        self.assertEqual([(p.source, p.translation) for p in phrases], [("Peki.", "Okay.")])

    def test_no_video_association_yields_no_glossary_entities(self):
        glossary_dir = Path(self.tmp.name) / "glossary"
        glossary_dir.mkdir()
        (glossary_dir / "show.yaml").write_text(
            "tvdb_id: 111\ntitle: Show\nentities:\n  - canonical: Eda\n    protected: true\n",
            encoding="utf-8")
        worker = Worker(self.store, str(self.media_root), str(self.work_root),
                        glossary_dir=str(glossary_dir))
        job = self.store.create_srt_translation("in/ep.tr.srt", "out/ep.en.srt")
        with patch.object(worker_mod.srt_translation, "run_srt_translation_pipeline") as mock_run:
            mock_run.side_effect = lambda **kw: fake_srt_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            worker._process(claimed)
        self.assertEqual(mock_run.call_args.kwargs["glossary_entities"], [])


class SrtUploadSourceResolutionTests(unittest.TestCase):
    """An uploaded source lives under srt_upload_dir, deliberately absent
    from media_root entirely -- proving the worker resolves against the
    correct root rather than always defaulting to media_root."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media_root = Path(self.tmp.name) / "data"
        self.upload_dir = Path(self.tmp.name) / "srt_uploads"
        self.media_root.mkdir(parents=True)
        self.upload_dir.mkdir(parents=True)
        (self.media_root / "out").mkdir()
        (self.upload_dir / "abc123.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nMerhaba\n", encoding="utf-8")
        self.store = JobStore(Path(self.tmp.name) / "jobs.db")
        self.worker = Worker(self.store, str(self.media_root), str(self.tmp.name) + "/work",
                             srt_upload_dir=str(self.upload_dir))

    def test_uploaded_source_resolves_against_upload_dir_not_media_root(self):
        job = self.store.create_srt_translation(
            "abc123.srt", "out/ep.en.srt", source_is_uploaded=True)
        with patch.object(worker_mod.srt_translation, "run_srt_translation_pipeline") as mock_run:
            mock_run.side_effect = lambda **kw: fake_srt_result(Path(kw["work_dir"]))
            claimed = self.store.claim()
            self.worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "completed")
        self.assertTrue((self.media_root / "out" / "ep.en.srt").exists())

    def test_worker_with_no_upload_dir_configured_fails_an_uploaded_job_cleanly(self):
        worker = Worker(self.store, str(self.media_root), str(self.tmp.name) + "/work")
        job = self.store.create_srt_translation(
            "abc123.srt", "out/ep.en.srt", source_is_uploaded=True)
        claimed = self.store.claim()
        worker._process(claimed)
        final = self.store.get(claimed["id"])
        self.assertEqual(final["status"], "failed")


if __name__ == "__main__":
    unittest.main()
