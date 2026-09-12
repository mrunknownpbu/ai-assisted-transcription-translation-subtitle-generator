"""Audio-stream selection at the pipeline layer: AUTO recommendation vs
MANUAL override, and the provenance fields pipeline.run() records around
which stream was used and why.

asr_transcribe()/translate_spans() are mocked -- these tests are about
stream selection and PipelineResult's stream-provenance fields, not about
faster-whisper/NLLB themselves (see the real S01E01 GPU run for that).
media.probe()/extract_audio()/audio_streams.extract_sample() run for real
against a small multi-stream fixture built with ffmpeg.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline
from transcript import CanonicalTranscript, ModelInfo, Segment, Word


def _make_two_stream_fixture(path: Path) -> None:
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=2",
        "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=2",
        "-map", "0:a", "-map", "1:a", "-c:a", "pcm_s16le",
        "-metadata:s:a:0", "language=tur", "-metadata:s:a:1", "language=eng",
        "-disposition:a:0", "default", "-disposition:a:1", "0",
        str(path),
    ], check=True, capture_output=True)


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
class StreamSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = Path(self.tmp.name) / "video.mkv"
        _make_two_stream_fixture(self.video)
        self.work_dir = Path(self.tmp.name) / "work"

    def _sampler_by_stream(self, mapping: dict[int, tuple[str, float]]):
        def sample(wav_path: Path) -> tuple[str, float]:
            index = int(wav_path.stem.rsplit("_", 1)[1])
            return mapping[index]
        return sample

    def _run(self, transcript, **kw):
        fake_translate = lambda cues, spans, src_lang, **_kw: ["translated"] * len(spans)
        kw.setdefault("stream_sampler", self._sampler_by_stream({0: ("tr", 0.95), 1: ("en", 0.9)}))
        with patch.object(pipeline, "asr_transcribe", return_value=transcript), \
             patch.object(pipeline.translate, "translate_spans", side_effect=fake_translate):
            return pipeline.run(
                video_path=str(self.video), media_root=str(self.tmp.name),
                work_dir=str(self.work_dir), write_output=False, **kw)

    def test_auto_stream_mode_recommends_and_selects_stream_zero(self):
        result = self._run(_fake_transcript("tr", 0.95))
        self.assertEqual(result.selected_audio_stream, 0)
        self.assertEqual(result.stream_selection_mode, "AUTO")
        self.assertIsNone(result.requested_audio_stream)
        self.assertEqual(result.embedded_stream_language, "tur")

    def test_manual_stream_selection_uses_exactly_the_requested_index(self):
        # Even though stream 0 would normally be recommended (default +
        # matching embedded tag), an explicit request for stream 1 must
        # never be reranked or silently switched.
        result = self._run(_fake_transcript("en", 0.9), audio_stream_index=1)
        self.assertEqual(result.selected_audio_stream, 1)
        self.assertEqual(result.stream_selection_mode, "MANUAL")
        self.assertEqual(result.requested_audio_stream, 1)
        self.assertEqual(result.embedded_stream_language, "eng")

    def test_manual_stream_selection_skips_candidate_sampling_entirely(self):
        # No sampler should even be consulted in MANUAL mode -- passing
        # one that raises proves it's never called.
        def exploding_sampler(wav_path):
            raise AssertionError("sampler must not run when a stream is manually selected")
        result = self._run(_fake_transcript("en", 0.9), audio_stream_index=1,
                           stream_sampler=exploding_sampler)
        self.assertEqual(result.selected_audio_stream, 1)

    def test_selection_reason_is_recorded_for_auto(self):
        result = self._run(_fake_transcript("tr", 0.95))
        self.assertTrue(result.selected_stream_reason)
        self.assertNotEqual(result.selected_stream_reason, "manually selected")

    def test_selection_reason_is_recorded_for_manual(self):
        result = self._run(_fake_transcript("en", 0.9), audio_stream_index=1)
        self.assertEqual(result.selected_stream_reason, "manually selected")

    def test_asr_receives_stream_provenance_to_record_on_the_transcript(self):
        # asr_transcribe() is mocked (its own passthrough into
        # CanonicalTranscript is asr.py's job, exercised by the real
        # S01E01 GPU run) -- this checks pipeline.run() actually PASSES
        # the resolved stream provenance down to it.
        transcript = _fake_transcript("tr", 0.95)
        fake_translate = lambda cues, spans, src_lang, **_kw: ["translated"] * len(spans)
        with patch.object(pipeline, "asr_transcribe", return_value=transcript) as mock_asr, \
             patch.object(pipeline.translate, "translate_spans", side_effect=fake_translate):
            pipeline.run(
                video_path=str(self.video), media_root=str(self.tmp.name),
                work_dir=str(self.work_dir), write_output=False,
                stream_sampler=self._sampler_by_stream({0: ("tr", 0.95), 1: ("en", 0.9)}))
        self.assertEqual(mock_asr.call_args.kwargs["embedded_stream_language"], "tur")
        self.assertEqual(mock_asr.call_args.kwargs["stream_selection_mode"], "AUTO")
        self.assertTrue(mock_asr.call_args.kwargs["stream_selection_reason"])

    def test_manual_stream_index_that_does_not_exist_fails_clearly(self):
        from media import MediaError
        with self.assertRaises(MediaError):
            self._run(_fake_transcript("tr", 0.95), audio_stream_index=99)

    def test_wrong_embedded_metadata_does_not_silently_force_that_language(self):
        # Stream 0 is tagged "tur" but the sampler says the actual speech
        # is English -- the pipeline must transcribe using what ASR (the
        # mocked full transcription here) actually reports, not the tag.
        result = self._run(_fake_transcript("en", 0.9),
                           stream_sampler=self._sampler_by_stream({0: ("en", 0.9), 1: ("en", 0.5)}))
        self.assertEqual(result.selected_audio_stream, 0)
        self.assertEqual(result.embedded_stream_language, "tur")
        self.assertEqual(result.detected_source_language, "en")


if __name__ == "__main__":
    unittest.main()
