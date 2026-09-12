"""ASR transcript cache, wired into pipeline.run() via transcript_cache_dir
(see transcript.auto_lookup_key/cache_key/validate_cached_transcript and
pipeline.py's ASR block). Dev/test only -- not wired into worker.py/api.py
for real jobs.

asr_transcribe() is mocked throughout, same convention as
test_pipeline_language.py: these tests are about whether pipeline.run()
correctly skips/uses/rejects a cache entry, not about faster-whisper
itself. media.probe_streams/extract_audio and media.content_fingerprint()
run for real against a tiny synthetic fixture, since the real media hash
is exactly what the cache key must be keyed on correctly.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import media
import pipeline
from asr import PIPELINE_VERSION
from transcript import CanonicalTranscript, ModelInfo, Segment, Word


def _make_fixture_video(path: Path, duration: float = 1.0) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=16000:cl=mono:d={duration}",
         "-c:a", "pcm_s16le", str(path)],
        check=True, capture_output=True)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not available")
class TranscriptCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = Path(self.tmp.name) / "video.mkv"
        _make_fixture_video(self.video)
        self.work_dir = Path(self.tmp.name) / "work"
        self.cache_dir = Path(self.tmp.name) / "transcript_cache"
        # The real value pipeline.run() will compute for this fixture --
        # a cached entry must be stored/validated against this, not a
        # placeholder, or every "hit" test would actually be exercising
        # validate_cached_transcript()'s mismatch-rejection path by accident.
        self.real_media_hash = media.content_fingerprint(self.video)

    def _fake_transcript(self, language="tr", probability=0.95, media_hash=None,
                         pipeline_version=None, model_name="faster-whisper-large-v3"):
        word = Word(text="hello", original_text="hello", start=0.0, end=0.5, probability=0.9)
        seg = Segment(index=0, start=0.0, end=0.5, words=[word], avg_logprob=-0.1,
                      no_speech_prob=0.0, compression_ratio=1.0)
        return CanonicalTranscript(
            media_path=str(self.video), media_hash=media_hash or self.real_media_hash,
            audio_stream_index=0, language=language, language_probability=probability,
            asr_model=ModelInfo(name=model_name, version="large-v3"), alignment_model=None,
            pipeline_version=pipeline_version or PIPELINE_VERSION, segments=[seg])

    def _run(self, asr_mock, **kw):
        fake_translate = lambda cues, spans, src_lang, **_kw: ["translated"] * len(spans)
        kw.setdefault("stream_sampler", lambda wav: ("tr", 0.9))
        kw.setdefault("transcript_cache_dir", str(self.cache_dir))
        with patch.object(pipeline, "asr_transcribe", asr_mock), \
             patch.object(pipeline.translate, "translate_spans", side_effect=fake_translate):
            return pipeline.run(
                video_path=str(self.video), media_root=str(self.tmp.name),
                work_dir=str(self.work_dir), write_output=False, **kw)

    def test_auto_mode_first_run_computes_and_caches_no_false_early_hit(self):
        asr_mock = unittest.mock.Mock(return_value=self._fake_transcript("tr", 0.95))
        result = self._run(asr_mock, source_lang="auto")
        self.assertEqual(asr_mock.call_count, 1, "first run must call ASR -- nothing cached yet")
        self.assertEqual(result.detected_source_language, "tr")
        # Exactly one entry now exists under the cache dir.
        self.assertEqual(len(list(self.cache_dir.glob("*.json"))), 1)

    def test_auto_mode_second_run_is_a_cache_hit(self):
        asr_mock = unittest.mock.Mock(return_value=self._fake_transcript("tr", 0.95))
        self._run(asr_mock, source_lang="auto")
        self.assertEqual(asr_mock.call_count, 1)

        result2 = self._run(asr_mock, source_lang="auto")
        self.assertEqual(asr_mock.call_count, 1, "second identical run must reuse the cache, not call ASR again")
        self.assertEqual(result2.detected_source_language, "tr")

    def test_manual_mode_cache_hit_and_miss(self):
        asr_mock = unittest.mock.Mock(return_value=self._fake_transcript("ja", 1.0))
        self._run(asr_mock, source_lang="ja")
        self.assertEqual(asr_mock.call_count, 1)

        # Same manual language again -> hit.
        self._run(asr_mock, source_lang="ja")
        self.assertEqual(asr_mock.call_count, 1, "repeat manual request for the same language must hit the cache")

        # A DIFFERENT manual language is a real ASR input (see
        # auto_lookup_key's docstring) -- must be a genuine miss, not
        # reuse whatever was cached under the other language's key.
        asr_mock2 = unittest.mock.Mock(return_value=self._fake_transcript("ko", 1.0))
        result3 = self._run(asr_mock2, source_lang="ko")
        self.assertEqual(asr_mock2.call_count, 1, "a different manual language must not reuse another language's cache entry")
        self.assertEqual(result3.detected_source_language, "ko")

    def test_stale_or_mismatched_cache_entry_is_invalidated_not_trusted(self):
        # First run populates a real, valid cache entry.
        asr_mock = unittest.mock.Mock(return_value=self._fake_transcript("tr", 0.95))
        self._run(asr_mock, source_lang="auto")
        self.assertEqual(asr_mock.call_count, 1)
        [cache_file] = list(self.cache_dir.glob("*.json"))

        # Corrupt it in place with a media_hash that doesn't match this
        # video -- simulating a hash-space collision, a hand-copied file,
        # or drift from a future change to what the key covers. This must
        # degrade to a clean miss (ASR runs again), never a silently wrong
        # transcript served from disk.
        stale = self._fake_transcript("tr", 0.95, media_hash="not-the-real-hash-at-all")
        stale.save(cache_file)

        asr_mock2 = unittest.mock.Mock(return_value=self._fake_transcript("tr", 0.95))
        result = self._run(asr_mock2, source_lang="auto")
        self.assertEqual(asr_mock2.call_count, 1, "a cache entry whose recorded media_hash doesn't match must be rejected")
        self.assertEqual(result.detected_source_language, "tr")

    def test_pipeline_version_mismatch_is_invalidated(self):
        asr_mock = unittest.mock.Mock(return_value=self._fake_transcript("tr", 0.95))
        self._run(asr_mock, source_lang="auto")
        [cache_file] = list(self.cache_dir.glob("*.json"))

        stale = self._fake_transcript("tr", 0.95, pipeline_version="0.0.1-old")
        stale.save(cache_file)

        asr_mock2 = unittest.mock.Mock(return_value=self._fake_transcript("tr", 0.95))
        self._run(asr_mock2, source_lang="auto")
        self.assertEqual(asr_mock2.call_count, 1, "a cache entry from a different pipeline_version must be rejected")

    def test_different_audio_stream_index_is_a_separate_cache_entry(self):
        # Simulates two different streams of the same file by forcing a
        # manual stream index and manual language for a single-stream
        # fixture -- the key must still differ per audio_stream_index even
        # when nothing else about the request changes (see
        # auto_lookup_key/cache_key's own field coverage).
        from transcript import auto_lookup_key
        key0 = auto_lookup_key(self.real_media_hash, 0, "pcm_s16le", "large-v3",
                               {"beam_size": 5, "temperature": [0.0], "condition_on_previous_text": False,
                                "vad_filter": True, "compute_type": "float16"}, None, PIPELINE_VERSION)
        key1 = auto_lookup_key(self.real_media_hash, 1, "pcm_s16le", "large-v3",
                               {"beam_size": 5, "temperature": [0.0], "condition_on_previous_text": False,
                                "vad_filter": True, "compute_type": "float16"}, None, PIPELINE_VERSION)
        self.assertNotEqual(key0, key1)

    def test_no_cache_dir_means_no_caching_at_all(self):
        # transcript_cache_dir=None (the default) must behave exactly as
        # before this feature existed -- every repeat call re-runs ASR.
        asr_mock = unittest.mock.Mock(return_value=self._fake_transcript("tr", 0.95))
        fake_translate = lambda cues, spans, src_lang, **_kw: ["translated"] * len(spans)
        with patch.object(pipeline, "asr_transcribe", asr_mock), \
             patch.object(pipeline.translate, "translate_spans", side_effect=fake_translate):
            for _ in range(2):
                pipeline.run(video_path=str(self.video), media_root=str(self.tmp.name),
                            work_dir=str(self.work_dir), write_output=False,
                            source_lang="auto", stream_sampler=lambda wav: ("tr", 0.9))
        self.assertEqual(asr_mock.call_count, 2, "no transcript_cache_dir must never cache across calls")


if __name__ == "__main__":
    unittest.main()
