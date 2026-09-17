"""Source-language auto-detection at the pipeline layer.

asr_transcribe() is mocked throughout -- these tests are about the
AUTO/MANUAL branching and result metadata pipeline.run() computes around
ASR, not about faster-whisper itself (see the real S01E01 GPU run for
that). media.probe_streams/extract_audio run for real against a tiny
synthetic fixture, exercising the actual audio-selection path.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline
from glossary import Entity
from transcript import CanonicalTranscript, ModelInfo, Segment, Word


def _make_fixture_video(path: Path, duration: float = 1.0) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=16000:cl=mono:d={duration}",
         "-c:a", "pcm_s16le", str(path)],
        check=True, capture_output=True)


def _fake_transcript(language: str, probability: float | None) -> CanonicalTranscript:
    word = Word(text="hello", original_text="hello", start=0.0, end=0.5, probability=0.9)
    seg = Segment(index=0, start=0.0, end=0.5, words=[word], avg_logprob=-0.1,
                  no_speech_prob=0.0, compression_ratio=1.0)
    return CanonicalTranscript(
        media_path="video.mkv", media_hash="hash", audio_stream_index=0,
        language=language, language_probability=probability,
        asr_model=ModelInfo(name="fake", version="1"), alignment_model=None,
        pipeline_version="test", segments=[seg])


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not available")
class AutoDetectLanguageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = Path(self.tmp.name) / "video.mkv"
        _make_fixture_video(self.video)
        self.work_dir = Path(self.tmp.name) / "work"

    def _run(self, transcript, **kw):
        # Translation is mocked too -- src_lang has already done its job by
        # the time translate_spans() is reached (transcript.language is
        # resolved); loading a real NLLB model has no bearing on the
        # auto-detect behavior under test here. stream_sampler is mocked
        # for the same reason on the audio-stream-recommendation side --
        # this fixture has exactly one audio stream, so whatever it
        # returns doesn't affect which stream gets chosen, only whether
        # the test needs a real GPU model (it must not).
        fake_translate = lambda cues, spans, src_lang, **_kw: ["translated"] * len(spans)
        kw.setdefault("stream_sampler", lambda wav: ("tr", 0.9))
        with patch.object(pipeline, "asr_transcribe", return_value=transcript), \
             patch.object(pipeline.translate, "translate_spans", side_effect=fake_translate):
            return pipeline.run(
                video_path=str(self.video), media_root=str(self.tmp.name),
                work_dir=str(self.work_dir), write_output=False, **kw)

    def test_default_source_lang_is_auto(self):
        result = self._run(_fake_transcript("tr", 0.95))
        self.assertEqual(result.requested_source_language, "auto")
        self.assertEqual(result.source_language_mode, "AUTO")

    def test_auto_resolves_to_turkish_for_turkish_audio(self):
        result = self._run(_fake_transcript("tr", 0.95), source_lang="auto")
        self.assertEqual(result.detected_source_language, "tr")

    def test_auto_resolves_to_another_supported_language(self):
        result = self._run(_fake_transcript("ja", 0.9), source_lang="auto")
        self.assertEqual(result.detected_source_language, "ja")

    def test_detected_language_persisted_separately_from_requested(self):
        result = self._run(_fake_transcript("tr", 0.95), source_lang="auto")
        self.assertEqual(result.requested_source_language, "auto")
        self.assertEqual(result.detected_source_language, "tr")

    def test_manual_override_forces_language_and_marks_mode_manual(self):
        result = self._run(_fake_transcript("tr", 1.0), source_lang="tr")
        self.assertEqual(result.source_language_mode, "MANUAL")
        self.assertEqual(result.requested_source_language, "tr")

    def test_manual_override_never_falsely_reports_auto(self):
        result = self._run(_fake_transcript("tr", 1.0), source_lang="tr")
        self.assertNotEqual(result.source_language_mode, "AUTO")

    def test_low_confidence_continue_is_the_default_and_does_not_raise(self):
        result = self._run(_fake_transcript("tr", 0.2), source_lang="auto")
        self.assertTrue(result.language_detection_uncertain)

    def test_low_confidence_require_override_raises(self):
        with self.assertRaises(pipeline.LowConfidenceLanguageError):
            self._run(_fake_transcript("tr", 0.2), source_lang="auto",
                     low_confidence_action="require_override")

    def test_manual_mode_is_never_flagged_uncertain_even_at_low_confidence(self):
        # A manual override is a deliberate human choice, not a detection --
        # there is nothing to be "uncertain" about.
        result = self._run(_fake_transcript("tr", 0.1), source_lang="tr")
        self.assertFalse(result.language_detection_uncertain)

    def test_unsupported_language_raises_clear_error_not_a_bare_keyerror(self):
        with self.assertRaises(pipeline.UnsupportedLanguageError):
            self._run(_fake_transcript("zz", 0.99), source_lang="auto")

    def test_detected_language_equal_to_target_skips_nllb_entirely(self):
        # English audio with an English target is a real reachable case
        # now that source language is auto-detected -- no NLLB call
        # should happen (nothing to translate), and translations must
        # equal the source sentences exactly.
        with patch.object(pipeline, "asr_transcribe", return_value=_fake_transcript("en", 0.98)), \
             patch.object(pipeline.translate, "translate_spans") as mock_translate:
            result = pipeline.run(
                video_path=str(self.video), media_root=str(self.tmp.name),
                work_dir=str(self.work_dir), write_output=False, source_lang="auto",
                stream_sampler=lambda wav: ("en", 0.9))
        mock_translate.assert_not_called()
        self.assertEqual(result.detected_source_language, "en")
        self.assertEqual(result.target_language, "en")

    def test_same_language_output_paths_collide_by_design(self):
        # This is exactly what worker.py's collision-handling relies on --
        # see test_worker.py's SameLanguageSourceAndTargetTests.
        fake_translate = lambda cues, spans, src_lang, **_kw: ["translated"] * len(spans)
        with patch.object(pipeline, "asr_transcribe", return_value=_fake_transcript("en", 0.98)), \
             patch.object(pipeline.translate, "translate_spans", side_effect=fake_translate):
            result = pipeline.run(
                video_path=str(self.video), media_root=str(self.tmp.name),
                work_dir=str(self.work_dir), write_output=True, source_lang="auto",
                stream_sampler=lambda wav: ("en", 0.9))
        self.assertEqual(result.source_srt_path, result.target_srt_path)

    def test_existing_translation_pipeline_receives_resolved_source_language(self):
        # A manual "ja" request and an AUTO run that detects "ja" must
        # both reach translation the same way -- via transcript.language,
        # never via the raw request string.
        result_manual = self._run(_fake_transcript("ja", 1.0), source_lang="ja")
        result_auto = self._run(_fake_transcript("ja", 0.9), source_lang="auto")
        self.assertEqual(result_manual.detected_source_language, result_auto.detected_source_language)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not available")
class GlossaryHotwordsTests(unittest.TestCase):
    """The same per-series glossary translation uses for entity
    protection is also fed to ASR as hotwords, biasing decoding toward
    correct name spelling -- built before glossary_map exists, since ASR
    runs first."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = Path(self.tmp.name) / "video.mkv"
        _make_fixture_video(self.video)
        self.work_dir = Path(self.tmp.name) / "work"

    def _run_and_capture_config(self, glossary_entities):
        fake_translate = lambda cues, spans, src_lang, **_kw: ["translated"] * len(spans)
        with patch.object(pipeline, "asr_transcribe",
                          return_value=_fake_transcript("tr", 0.95)) as mock_asr, \
             patch.object(pipeline.translate, "translate_spans", side_effect=fake_translate):
            pipeline.run(video_path=str(self.video), media_root=str(self.tmp.name),
                        work_dir=str(self.work_dir), write_output=False,
                        stream_sampler=lambda wav: ("tr", 0.9),
                        glossary_entities=glossary_entities)
        return mock_asr.call_args.kwargs["config"]

    def test_glossary_surface_forms_become_hotwords(self):
        entities = [Entity(canonical="Eda", surface_forms=["Eda"]),
                   Entity(canonical="Serkan", surface_forms=["Serkan", "Serkan Bolat"])]
        config = self._run_and_capture_config(entities)
        hotwords = set(config.hotwords.split(" "))
        self.assertIn("Eda", hotwords)
        self.assertIn("Serkan", hotwords)

    def test_no_glossary_entities_leaves_hotwords_unset(self):
        config = self._run_and_capture_config(None)
        self.assertIsNone(config.hotwords)


if __name__ == "__main__":
    unittest.main()
