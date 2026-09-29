"""pipeline.run() end to end for the cue-fragmentation fix (2026-09-29).

Real case, Hammer Session! S01E01 1881.2-1895.6s: ~14s of Japanese with no
real pause, cut by segmentation_source.MAX_DURATION into cues that
merge_groups() can't join (envelope > 7s), all in ONE translation span.
NLLB returned one English sentence, which was shown as three cues:
"Hey, can" / "you" / "read it?". ASR and translation are mocked; the
segmentation, span distribution, projection and coverage check are real.
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


def _japanese_transcript(duration: float, word_len: float = 0.8) -> CanonicalTranscript:
    words, t = [], 0.0
    while t + word_len <= duration + 1e-9:
        words.append(Word(text="あい", original_text="あい", start=round(t, 3),
                          end=round(t + word_len, 3), probability=0.9, joins_previous=bool(words)))
        t += word_len
    seg = Segment(index=0, start=0.0, end=words[-1].end, words=words, avg_logprob=-0.1,
                  no_speech_prob=0.0, compression_ratio=1.0, language="ja")
    return CanonicalTranscript(
        media_path="video.mkv", media_hash="hash", audio_stream_index=0, language="ja",
        language_probability=0.99, asr_model=ModelInfo(name="fake", version="1"),
        alignment_model=None, pipeline_version="test", segments=[seg])


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not available")
class OneSentenceOverSeveralGroupsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = Path(self.tmp.name) / "video.mkv"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=1",
                        "-c:a", "pcm_s16le", str(self.video)], check=True, capture_output=True)

    def _run(self, transcript, english):
        seen = {}

        def fake_translate(cues, spans, src_lang, **_kw):
            seen["spans"] = spans
            return [english] * len(spans)

        with patch.object(pipeline, "asr_transcribe", return_value=transcript), \
             patch.object(pipeline.translate, "translate_spans", side_effect=fake_translate):
            result = pipeline.run(video_path=str(self.video), media_root=self.tmp.name,
                                  work_dir=str(Path(self.tmp.name) / "work"), source_lang="ja",
                                  write_output=True, allow_overwrite=True,
                                  stream_sampler=lambda wav: ("ja", 0.9))
        return result, seen["spans"]

    def test_one_english_sentence_is_one_cue_not_three_fragments(self):
        result, spans = self._run(_japanese_transcript(14.4), "Hey, can you read it?")
        self.assertEqual(len(spans), 1)   # the precondition: one span over several groups
        import srt
        target = srt.parse(result.target_srt_path)
        self.assertEqual([c.text for c in target], ["Hey, can you read it?"])
        self.assertAlmostEqual(target[0].start, 0.0, places=2)
        # Coverage is checked against the full 0-14.4s envelope; the
        # displayed cue is then capped at segmentation_target.MAX_DURATION
        # by extend_short_cues(), as for any other cue.
        self.assertAlmostEqual(target[0].end, 7.0, places=2)
        self.assertEqual(result.qc.segmentation.flagged, 0)
        self.assertTrue(result.valid)

    def test_two_sentences_over_the_same_groups_stay_whole(self):
        result, _ = self._run(_japanese_transcript(14.4), "What!? You want to fight me?")
        import srt
        texts = [c.text for c in srt.parse(result.target_srt_path)]
        self.assertEqual(" ".join(texts), "What!? You want to fight me?")
        self.assertTrue(all(t.endswith(("?", "!")) for t in texts))
        self.assertEqual(result.qc.segmentation.flagged, 0)


if __name__ == "__main__":
    unittest.main()
