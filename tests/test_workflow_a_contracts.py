"""Workflow A (video -> English) contracts at the pipeline level, with ASR and
translation faked: audio is the only source of text, ASR failures surface,
hallucinated text never reaches translation, output is atomic."""
import builtins
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline
import srt
from errors import MediaError
from transcript import CanonicalTranscript, ModelInfo, Segment, Word

SIBLING_TEXT = "SIBLING-SUBTITLE-CANARY"


def make_video(path: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=1",
                    "-c:a", "pcm_s16le", str(path)], check=True, capture_output=True)


def transcript(texts, language="tr"):
    segments = []
    for i, text in enumerate(texts):
        words = [Word(text=w, original_text=w, start=i * 2.0 + k * 0.3, end=i * 2.0 + k * 0.3 + 0.25,
                      probability=0.95) for k, w in enumerate(text.split())]
        segments.append(Segment(index=i, start=words[0].start, end=words[-1].end, words=words,
                                avg_logprob=-0.1, no_speech_prob=0.05, compression_ratio=1.2))
    return CanonicalTranscript(
        media_path="video.mkv", media_hash="h", audio_stream_index=0, language=language,
        language_probability=0.99, asr_model=ModelInfo(name="fake", version="1"), alignment_model=None,
        pipeline_version="test", segments=segments)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not available")
class WorkflowAContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.video = self.root / "Show S01E01.mkv"
        make_video(self.video)
        self.work = self.root / "work"
        self.translated = []

    def fake_translate(self, cues, spans, src_lang, **kwargs):
        self.translated.append([c.text for c in cues])
        return [f"EN {i}" for i in range(len(spans))]

    def run_pipeline(self, asr_result=None, asr_error=None, **kwargs):
        asr = (patch.object(pipeline, "asr_transcribe", side_effect=asr_error) if asr_error
               else patch.object(pipeline, "asr_transcribe", return_value=asr_result))
        with asr, patch.object(pipeline.translate, "translate_spans", side_effect=self.fake_translate):
            return pipeline.run(video_path=str(self.video), media_root=str(self.root), work_dir=str(self.work),
                                source_lang="tr", write_output=True, allow_overwrite=True,
                                stream_sampler=lambda wav: ("tr", 0.9), **kwargs)

    def test_sibling_subtitles_are_never_read_or_used(self):
        """The pipeline may read its own scratch outputs (QC re-parses them),
        but no pre-existing subtitle beside the video."""
        siblings = [self.root / n for n in ("Show S01E01.tr.srt", "Show S01E01.en.srt", "Show S01E02.tr.srt")]
        for path in siblings:
            path.write_text(f"1\n00:00:00,000 --> 00:00:01,000\n{SIBLING_TEXT}\n\n", encoding="utf-8")
        guarded = {str(p) for p in siblings}
        touched = []
        real_open, real_read_text = builtins.open, Path.read_text

        def spy_open(file, *args, **kwargs):
            if str(file) in guarded:
                touched.append(("open", str(file)))
            return real_open(file, *args, **kwargs)

        def spy_read_text(self_, *args, **kwargs):
            if str(self_) in guarded:
                touched.append(("read_text", str(self_)))
            return real_read_text(self_, *args, **kwargs)

        with patch.object(builtins, "open", spy_open), patch.object(Path, "read_text", spy_read_text):
            result = self.run_pipeline(transcript(["Merhaba nasılsın bugün", "Ben iyiyim teşekkürler"]))
        self.assertEqual(touched, [])
        self.assertNotIn(SIBLING_TEXT, "".join(" ".join(t) for t in self.translated))
        self.assertNotIn(SIBLING_TEXT, result.source_srt_path.read_text(encoding="utf-8"))
        self.assertNotIn(SIBLING_TEXT, result.target_srt_path.read_text(encoding="utf-8"))
        for path in siblings:   # and they were not modified either
            self.assertIn(SIBLING_TEXT, path.read_text(encoding="utf-8"))

    def test_the_transcript_text_comes_only_from_the_audio_result(self):
        self.run_pipeline(transcript(["Merhaba nasılsın bugün"]))
        self.assertEqual(self.translated, [["Merhaba nasılsın bugün"]])

    def test_an_asr_failure_propagates_and_writes_nothing(self):
        with self.assertRaises(RuntimeError):
            self.run_pipeline(asr_error=RuntimeError("whisper died"))
        self.assertEqual(self.translated, [])
        self.assertFalse(self.work.exists() and list(self.work.glob("*.srt")))

    def test_a_media_error_is_a_typed_error(self):
        broken = self.root / "broken.mkv"
        broken.write_bytes(b"not a video")
        self.video = broken
        with self.assertRaises(MediaError):
            self.run_pipeline(transcript(["x y z"]))

    def test_hallucinated_signature_text_never_reaches_translation(self):
        result = self.run_pipeline(transcript(
            ["Merhaba nasılsın bugün", "Altyazı M.K.", "Ben iyiyim teşekkürler"]))
        self.assertNotIn("Altyazı", " ".join(" ".join(t) for t in self.translated))
        self.assertTrue(result.valid)

    def test_outputs_are_valid_srt_with_qc_run_and_ordered_timing(self):
        result = self.run_pipeline(transcript(["Merhaba nasılsın bugün", "Ben iyiyim teşekkürler"]))
        english = srt.parse(result.target_srt_path)
        self.assertTrue(english)
        self.assertTrue(all(a.end <= b.start + 1e-6 for a, b in zip(english, english[1:])))
        self.assertIsNotNone(result.qc.timing)
        self.assertIsNotNone(result.qc.output)
        self.assertEqual(result.source_srt_path.name, "Show S01E01.tr.srt")
        self.assertEqual(result.target_srt_path.name, "Show S01E01.en.srt")

    def test_glossary_entities_are_passed_to_translation_but_not_to_asr_as_mined_text(self):
        from glossary import Entity
        seen = {}

        def fake(cues, spans, src_lang, **kwargs):
            seen["glossary_map"] = kwargs.get("glossary_map")
            return ["EN"] * len(spans)

        with patch.object(pipeline, "asr_transcribe", return_value=transcript(["Ateş geldi bugün"])), \
             patch.object(pipeline.translate, "translate_spans", side_effect=fake):
            pipeline.run(video_path=str(self.video), media_root=str(self.root), work_dir=str(self.work),
                         source_lang="tr", stream_sampler=lambda wav: ("tr", 0.9),
                         glossary_entities=[Entity(canonical="Ateş", surface_forms=["Ateş"])])
        self.assertTrue(seen["glossary_map"])

    def test_a_failed_write_leaves_the_existing_output_intact(self):
        import output
        self.work.mkdir()
        existing = self.work / "Show S01E01.en.srt"
        existing.write_text("KEEP ME", encoding="utf-8")
        real = output.os.replace

        def fail(src, dst):
            if str(dst).endswith(".en.srt"):
                raise OSError("disk full")
            return real(src, dst)

        with patch.object(output.os, "replace", side_effect=fail), self.assertRaises(OSError):
            self.run_pipeline(transcript(["Merhaba nasılsın bugün"]))
        self.assertEqual(existing.read_text(encoding="utf-8"), "KEEP ME")
        self.assertEqual([p for p in self.work.iterdir() if ".tmp-" in p.name], [])


if __name__ == "__main__":
    unittest.main()
