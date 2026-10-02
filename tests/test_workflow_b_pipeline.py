"""Workflow B (existing subtitle -> English) end to end at the pipeline level:
srt_translation.run_srt_translation_pipeline with translation faked. The
worker tests patch this function out, so these are its only direct tests."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import glossary as glossary_mod
import srt
import srt_translation as st
from errors import LowConfidenceLanguageError, SubtitleParseError, UnsupportedLanguageError

TURKISH = [
    (1.0, 3.5, "Bugün hava çok güzel, dışarı çıkmak istiyorum."),
    (4.0, 7.0, "Sen de benimle gelmek ister misin, yoksa evde mi kalırsın?"),
    (8.0, 11.0, "Akşam yemeğinde annemin yaptığı çorbayı yemek istiyorum."),
    (12.0, 14.0, "Çok teşekkür ederim, yarın görüşürüz."),
]


def write_source(path: Path, cues=TURKISH, *, vtt=False) -> Path:
    if vtt:
        body = "WEBVTT\n\n" + "\n\n".join(
            f"{srt._ts(s).replace(',', '.')} --> {srt._ts(e).replace(',', '.')}\n{t}" for s, e, t in cues) + "\n"
    else:
        body = srt.render([srt.SrtCue(s, e, t) for s, e, t in cues])
    path.write_text(body, encoding="utf-8")
    return path


def fake_translate(cues, spans, src_lang, **kwargs):
    fake_translate.calls.append({"src_lang": src_lang, "texts": [c.text for c in cues], **kwargs})
    return [f"EN: {cues[i].text}" for [i] in spans]


fake_translate.calls = []


class WorkflowBTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = write_source(self.root / "Show S01E01.srt")
        self.work = self.root / "work"
        fake_translate.calls = []
        patcher = patch.object(st.translate, "translate_spans", side_effect=fake_translate)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_pipeline(self, **kwargs):
        kwargs.setdefault("source_lang", "tr")
        return st.run_srt_translation_pipeline(self.source, self.work, **kwargs)


class OutputTests(WorkflowBTestCase):
    def test_writes_english_and_preserves_the_original_beside_it(self):
        result = self.run_pipeline()
        self.assertEqual(result.target_srt_path.name, "Show S01E01.en.srt")
        self.assertEqual(result.source_language_srt_path.name, "Show S01E01.tr.srt")
        original = [(c.start, c.end, c.text) for c in srt.parse(result.source_language_srt_path)]
        self.assertEqual(original, TURKISH)
        self.assertTrue(all(c.text.startswith("EN:") for c in srt.parse(result.target_srt_path)))

    def test_the_source_file_is_never_modified(self):
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.run_pipeline()
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)

    def test_write_output_false_writes_nothing(self):
        result = self.run_pipeline(write_output=False)
        self.assertIsNone(result.target_srt_path)
        self.assertFalse(self.work.exists() and any(self.work.iterdir()))

    def test_an_existing_output_is_kept_unless_overwrite_is_allowed(self):
        self.work.mkdir()
        existing = self.work / "Show S01E01.en.srt"
        existing.write_text("KEEP ME", encoding="utf-8")
        self.run_pipeline(allow_overwrite=False)
        self.assertEqual(existing.read_text(encoding="utf-8"), "KEEP ME")
        self.run_pipeline(allow_overwrite=True)
        self.assertIn("EN:", existing.read_text(encoding="utf-8"))

    def test_a_failure_leaves_existing_output_and_no_temp_files(self):
        self.work.mkdir()
        existing = self.work / "Show S01E01.en.srt"
        existing.write_text("KEEP ME", encoding="utf-8")
        with patch.object(st.translate, "translate_spans", side_effect=RuntimeError("model died")), \
             self.assertRaises(RuntimeError):
            self.run_pipeline(allow_overwrite=True)
        self.assertEqual(existing.read_text(encoding="utf-8"), "KEEP ME")
        self.assertEqual(sorted(p.name for p in self.work.iterdir()), ["Show S01E01.en.srt"])


    def test_a_failed_write_never_corrupts_an_existing_output(self):
        import output
        self.work.mkdir()
        existing = self.work / "Show S01E01.en.srt"
        existing.write_text("KEEP ME", encoding="utf-8")
        real_replace = output.os.replace

        def fail_for_english(src, dst):
            if str(dst).endswith(".en.srt"):
                raise OSError("disk full")
            return real_replace(src, dst)

        with patch.object(output.os, "replace", side_effect=fail_for_english), self.assertRaises(OSError):
            self.run_pipeline(allow_overwrite=True)
        self.assertEqual(existing.read_text(encoding="utf-8"), "KEEP ME")
        leftovers = [p.name for p in self.work.iterdir() if ".tmp-" in p.name]
        self.assertEqual(leftovers, [])


class TimingTests(WorkflowBTestCase):
    def test_every_english_cue_stays_inside_its_source_cue_envelope(self):
        result = self.run_pipeline()
        english = srt.parse(result.target_srt_path)
        for start, end, _ in TURKISH:
            inside = [c for c in english if c.start >= start - 1e-6 and c.end <= end + 1e-6]
            self.assertTrue(inside, f"no English cue inside {start}-{end}")
        self.assertGreaterEqual(min(c.start for c in english), TURKISH[0][0] - 1e-6)
        self.assertLessEqual(max(c.end for c in english), TURKISH[-1][1] + 1e-6)

    def test_no_offset_is_applied(self):
        english = srt.parse(self.run_pipeline().target_srt_path)
        self.assertAlmostEqual(english[0].start, TURKISH[0][0], places=2)


class LanguageTests(WorkflowBTestCase):
    def test_auto_detects_the_language_from_the_text(self):
        result = self.run_pipeline(source_lang="auto")
        self.assertEqual(result.detected_source_language, "tr")
        self.assertEqual(fake_translate.calls[0]["src_lang"], "tr")
        self.assertGreater(result.language_probability, 0.5)

    def test_low_confidence_detection_demands_a_manual_language(self):
        with patch.object(st.langid, "detect_text_language", return_value=("tr", 0.2)), \
             self.assertRaises(LowConfidenceLanguageError):
            self.run_pipeline(source_lang="auto")
        self.assertEqual(fake_translate.calls, [])

    def test_unsupported_language_fails_before_translating(self):
        with self.assertRaises(UnsupportedLanguageError):
            self.run_pipeline(source_lang="xx")
        self.assertEqual(fake_translate.calls, [])

    def test_a_source_already_in_english_is_not_translated(self):
        result = self.run_pipeline(source_lang="en")
        self.assertEqual(fake_translate.calls, [])
        self.assertEqual([c.text for c in srt.parse(result.target_srt_path)][0], TURKISH[0][2])
        self.assertIn("TRANSLATION_SKIPPED", [name for name, _ in result.events])


class NoAsrTests(WorkflowBTestCase):
    def test_asr_is_never_invoked(self):
        import asr
        with patch.object(asr, "transcribe", side_effect=AssertionError("ASR ran in Workflow B")), \
             patch.object(asr, "WhisperModel", side_effect=AssertionError("Whisper loaded"), create=True):
            self.run_pipeline()
            self.run_pipeline(source_lang="auto")


class ParsingTests(WorkflowBTestCase):
    def test_malformed_source_fails_and_writes_nothing(self):
        self.source.write_text("1\nnot a timing line\ntext\n", encoding="utf-8")
        with self.assertRaises(SubtitleParseError):
            self.run_pipeline()
        self.assertFalse(self.work.exists() and any(self.work.iterdir()))

    def test_webvtt_source_is_accepted(self):
        source = write_source(self.root / "vtt.srt", vtt=True)
        self.source = source
        result = self.run_pipeline()
        self.assertEqual(len(srt.parse(result.source_language_srt_path)), len(TURKISH))


class GlossaryAndQcTests(WorkflowBTestCase):
    def test_glossary_entities_reach_translation(self):
        entity = glossary_mod.Entity(canonical="Ateş", surface_forms=["Ateş"])
        self.run_pipeline(glossary_entities=[entity])
        self.assertTrue(fake_translate.calls[0]["glossary_map"])

    def test_qc_runs_and_a_clean_job_is_valid(self):
        result = self.run_pipeline()
        self.assertIsNotNone(result.qc.translation)
        self.assertIsNotNone(result.qc.timing)
        self.assertIsNotNone(result.qc.readability)
        self.assertIsNotNone(result.qc.output)
        self.assertTrue(result.valid)
        self.assertEqual([n for n, _ in result.events][-1], "JOB_COMPLETED")

    def test_progress_and_stage_events_are_reported(self):
        seen = []
        self.run_pipeline(on_event=lambda name, data: seen.append(name))
        for expected in ("SRT_PARSE_COMPLETED", "TRANSLATION_STARTED", "TARGET_SEGMENTATION_COMPLETED",
                         "QC_COMPLETED", "OUTPUT_COMMITTED", "JOB_COMPLETED"):
            self.assertIn(expected, seen)


if __name__ == "__main__":
    unittest.main()
