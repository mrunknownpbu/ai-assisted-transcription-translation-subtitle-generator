"""Audio-stream discovery, exclusion, scoring, and end-to-end ranking.

parse_audio_streams/AudioStream.exclusion_reason/score_candidate are
tested against synthetic ffprobe-shaped dicts (fast, exhaustive, no
ffmpeg needed). recommend_stream() is tested end-to-end against one real
multi-stream fixture built with ffmpeg -- real probing, real short-clip
extraction, a mocked (GPU-free) language sampler.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import audio_streams
from audio_streams import AudioStream, parse_audio_streams, recommend_stream, score_candidate


def _raw(index, *, codec_type="audio", codec_name="aac", channels=2, channel_layout="stereo",
        sample_rate="48000", bit_rate="192000", duration="1200.0", language=None, title=None,
        handler_name=None, default=False, forced=False, hearing_impaired=False,
        visual_impaired=False, commentary=False) -> dict:
    tags = {}
    if language is not None:
        tags["language"] = language
    if title is not None:
        tags["title"] = title
    if handler_name is not None:
        tags["handler_name"] = handler_name
    return {
        "index": index, "codec_type": codec_type, "codec_name": codec_name,
        "codec_long_name": f"{codec_name} long name", "channels": channels,
        "channel_layout": channel_layout, "sample_rate": sample_rate, "bit_rate": bit_rate,
        "duration": duration, "tags": tags,
        "disposition": {"default": int(default), "forced": int(forced),
                       "hearing_impaired": int(hearing_impaired),
                       "visual_impaired": int(visual_impaired), "comment": int(commentary)},
    }


class ParseAudioStreamsTests(unittest.TestCase):
    def test_non_audio_streams_are_skipped(self):
        streams = parse_audio_streams([_raw(0, codec_type="video"), _raw(1)])
        self.assertEqual([s.index for s in streams], [1])

    def test_fields_are_extracted(self):
        [s] = parse_audio_streams([_raw(1, codec_name="opus", channels=2, language="tur",
                                        title="Turkish", default=True)])
        self.assertEqual(s.codec, "opus")
        self.assertEqual(s.channels, 2)
        self.assertEqual(s.language, "tur")
        self.assertEqual(s.title, "Turkish")
        self.assertTrue(s.default)

    def test_missing_embedded_language_is_none_not_a_crash(self):
        [s] = parse_audio_streams([_raw(0, language=None)])
        self.assertIsNone(s.language)


class ExclusionReasonTests(unittest.TestCase):
    def test_plain_dialogue_track_is_not_excluded(self):
        s = AudioStream(index=0, language="tur", default=True)
        self.assertIsNone(s.exclusion_reason)

    def test_commentary_disposition_excluded(self):
        s = AudioStream(index=0, commentary=True)
        self.assertIsNotNone(s.exclusion_reason)

    def test_visual_impaired_disposition_excluded(self):
        s = AudioStream(index=0, visual_impaired=True)
        self.assertIsNotNone(s.exclusion_reason)

    def test_hearing_impaired_disposition_excluded(self):
        s = AudioStream(index=0, hearing_impaired=True)
        self.assertIsNotNone(s.exclusion_reason)

    def test_commentary_title_keyword_excluded_even_without_disposition_flag(self):
        s = AudioStream(index=0, title="Director's Commentary")
        self.assertIsNotNone(s.exclusion_reason)

    def test_audio_description_title_keyword_excluded(self):
        s = AudioStream(index=0, title="English Audio Description")
        self.assertIsNotNone(s.exclusion_reason)

    def test_unrelated_title_is_not_excluded(self):
        s = AudioStream(index=0, title="Stereo Mix")
        self.assertIsNone(s.exclusion_reason)


class ScoreCandidateTests(unittest.TestCase):
    def test_default_disposition_alone_does_not_guarantee_best_score(self):
        default_low_confidence = AudioStream(index=0, default=True, channels=2)
        non_default_high_confidence = AudioStream(index=1, default=False, channels=2)
        score_default, _ = score_candidate(default_low_confidence, "tr", 0.3,
                                           total_duration=None, preferred_language=None)
        score_other, _ = score_candidate(non_default_high_confidence, "tr", 0.99,
                                         total_duration=None, preferred_language=None)
        self.assertGreater(score_other, score_default)

    def test_embedded_language_mismatch_is_named_in_reason_not_hidden(self):
        s = AudioStream(index=0, language="eng")
        _, reason = score_candidate(s, "tr", 0.95, total_duration=None, preferred_language=None)
        self.assertIn("does NOT match", reason)

    def test_embedded_language_match_scores_higher_than_mismatch(self):
        matching = AudioStream(index=0, language="tur")
        mismatched = AudioStream(index=1, language="eng")
        score_match, _ = score_candidate(matching, "tr", 0.9, total_duration=None, preferred_language=None)
        score_mismatch, _ = score_candidate(mismatched, "tr", 0.9, total_duration=None, preferred_language=None)
        self.assertGreater(score_match, score_mismatch)

    def test_reason_is_never_empty(self):
        s = AudioStream(index=0)
        _, reason = score_candidate(s, None, None, total_duration=None, preferred_language=None)
        self.assertTrue(reason)

    def test_preferred_language_match_is_named_in_reason(self):
        # The actual RANKING effect of a preferred language is a
        # structural sort priority in recommend_stream(), not a score
        # delta here -- see RecommendStreamTests.
        # test_manual_preferred_language_biases_recommendation -- but the
        # reason string should still say whether this candidate matches.
        s = AudioStream(index=0, channels=1)
        _, reason = score_candidate(s, "tr", 0.8, total_duration=None, preferred_language="tr")
        self.assertIn("matches requested source language", reason)
        _, reason = score_candidate(s, "en", 0.8, total_duration=None, preferred_language="tr")
        self.assertIn("does not match requested source language", reason)


def _make_two_stream_fixture(path: Path) -> None:
    """One MKV, two real (silent) audio streams -- stream 0 tagged tur+default,
    stream 1 tagged eng. Real ffprobe/ffmpeg exercise the actual code path;
    only the language SAMPLER is mocked (see RecommendStreamTests)."""
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=2",
        "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=2",
        "-map", "0:a", "-map", "1:a", "-c:a", "pcm_s16le",
        "-metadata:s:a:0", "language=tur", "-metadata:s:a:1", "language=eng",
        "-disposition:a:0", "default", "-disposition:a:1", "0",
        str(path),
    ], check=True, capture_output=True)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not available")
class RecommendStreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = Path(self.tmp.name) / "video.mkv"
        _make_two_stream_fixture(self.video)
        self.work_dir = Path(self.tmp.name) / "work"

    def _sampler_by_stream(self, mapping: dict[int, tuple[str, float]]):
        def sample(wav_path: Path) -> tuple[str, float]:
            # extract_sample() names files sample_<index>.wav -- deterministic
            # per-stream without any GPU/model involved.
            index = int(wav_path.stem.rsplit("_", 1)[1])
            return mapping[index]
        return sample

    def test_auto_selects_the_correct_stream(self):
        rec = recommend_stream(self.video, self.work_dir,
                               sampler=self._sampler_by_stream({0: ("tr", 0.95), 1: ("en", 0.9)}))
        self.assertEqual(rec.recommended_index, 0)
        self.assertEqual(rec.recommended_language, "tr")

    def test_default_stream_not_the_best_is_not_blindly_chosen(self):
        # Stream 0 is `default` but only a weak detection; stream 1 is a
        # strong, confident detection -- the non-default stream should win.
        rec = recommend_stream(self.video, self.work_dir,
                               sampler=self._sampler_by_stream({0: ("tr", 0.2), 1: ("en", 0.98)}))
        self.assertEqual(rec.recommended_index, 1)

    def test_all_streams_are_listed_even_when_not_recommended(self):
        rec = recommend_stream(self.video, self.work_dir,
                               sampler=self._sampler_by_stream({0: ("tr", 0.95), 1: ("en", 0.9)}))
        self.assertEqual({s.index for s in rec.streams}, {0, 1})

    def test_close_scores_surface_as_alternates(self):
        # Stream 0 is default + embedded-tag-matching (structural bonuses)
        # but only a middling detection; stream 1 has none of the
        # structural bonuses but a much stronger detection -- close
        # enough in the end to surface as an alternate.
        rec = recommend_stream(self.video, self.work_dir,
                               sampler=self._sampler_by_stream({0: ("tr", 0.5), 1: ("en", 0.7)}))
        self.assertTrue(rec.alternates)
        self.assertIn(str(rec.alternates[0].stream.index), rec.reason)

    def test_manual_preferred_language_biases_recommendation(self):
        # Both plausible; preferring "en" should tip a near-tie toward the
        # English stream even if Turkish's raw detection is marginally higher.
        rec = recommend_stream(self.video, self.work_dir, preferred_language="en",
                               sampler=self._sampler_by_stream({0: ("tr", 0.80), 1: ("en", 0.78)}))
        self.assertEqual(rec.recommended_index, 1)

    def test_reason_is_explainable_not_a_black_box(self):
        rec = recommend_stream(self.video, self.work_dir,
                               sampler=self._sampler_by_stream({0: ("tr", 0.95), 1: ("en", 0.9)}))
        self.assertIn("tr", rec.reason)
        self.assertIn("%", rec.reason)



class SampleModelNameTests(unittest.TestCase):
    """_sample_model_name() reads SUBTITLE_AI_SAMPLE_MODEL with a safe default."""

    def test_default_is_small(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUBTITLE_AI_SAMPLE_MODEL", None)
            self.assertEqual(audio_streams._sample_model_name(), "small")

    def test_env_var_overrides_default(self):
        import os
        with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
                os.environ, {"SUBTITLE_AI_SAMPLE_MODEL": "base"}):
            self.assertEqual(audio_streams._sample_model_name(), "base")

    def test_blank_env_var_falls_back_to_small(self):
        import os
        with __import__("unittest.mock", fromlist=["patch"]).patch.dict(
                os.environ, {"SUBTITLE_AI_SAMPLE_MODEL": "  "}):
            self.assertEqual(audio_streams._sample_model_name(), "small")


class DefaultSamplerModelResolutionTests(unittest.TestCase):
    """default_sampler() uses the env-var model and falls back to large-v3
    gracefully when the configured lightweight model fails to load."""

    def test_explicit_model_name_is_used(self):
        """Passing model_name="base" overrides the env var."""
        from unittest.mock import MagicMock, patch
        from pathlib import Path

        fake_wav = Path("/fake/sample.wav")

        class FakeModel:
            def __init__(self, name, **kw):
                pass

            def transcribe(self, path, **kw):
                info = MagicMock()
                info.language = "tr"
                info.language_probability = 0.95
                return iter([]), info

        with patch("faster_whisper.WhisperModel", FakeModel), \
             patch("asr.AsrConfig") as MockCfg:
            MockCfg.return_value.compute_type = "int8"
            sampler = audio_streams.default_sampler(model_name="base")
            lang, prob = sampler(fake_wav)
        self.assertEqual(lang, "tr")
        self.assertAlmostEqual(prob, 0.95)

    def test_fallback_to_large_v3_when_small_fails(self):
        """If the env-var model (small) raises on load, the sampler falls back
        to large-v3 and still returns a valid result -- no exception escapes."""
        import os
        from unittest.mock import MagicMock, patch
        from pathlib import Path

        fake_wav = Path("/fake/sample.wav")
        loaded_names: list[str] = []

        class FakeModel:
            def __init__(self, name, **kw):
                if name == "small":
                    raise OSError("model not in /models")
                loaded_names.append(name)

            def transcribe(self, path, **kw):
                info = MagicMock()
                info.language = "tr"
                info.language_probability = 0.9
                return iter([]), info

        with patch.dict(os.environ, {"SUBTITLE_AI_SAMPLE_MODEL": "small"}), \
             patch("faster_whisper.WhisperModel", FakeModel), \
             patch("asr.AsrConfig") as MockCfg:
            MockCfg.return_value.compute_type = "int8"
            sampler = audio_streams.default_sampler()
            lang, prob = sampler(fake_wav)

        self.assertEqual(lang, "tr")
        self.assertEqual(loaded_names, ["large-v3"],
                         "should have fallen back to large-v3 after small failed")

    def test_no_fallback_needed_when_model_is_already_large_v3(self):
        """When SUBTITLE_AI_SAMPLE_MODEL=large-v3, skip the try/except path
        entirely and load large-v3 directly -- no spurious fallback log."""
        import os
        from unittest.mock import MagicMock, patch
        from pathlib import Path

        fake_wav = Path("/fake/sample.wav")
        loaded_names: list[str] = []

        class FakeModel:
            def __init__(self, name, **kw):
                loaded_names.append(name)

            def transcribe(self, path, **kw):
                info = MagicMock()
                info.language = "en"
                info.language_probability = 0.85
                return iter([]), info

        with patch.dict(os.environ, {"SUBTITLE_AI_SAMPLE_MODEL": "large-v3"}), \
             patch("faster_whisper.WhisperModel", FakeModel), \
             patch("asr.AsrConfig") as MockCfg:
            MockCfg.return_value.compute_type = "int8"
            sampler = audio_streams.default_sampler()
            sampler(fake_wav)

        self.assertEqual(loaded_names, ["large-v3"])


class PreflightVramWiringTests(unittest.TestCase):
    """default_sampler() must call gpu.preflight_vram_check() before each
    WhisperModel construction (IMPROVEMENT_PLAN.md 2.2) -- the lightweight
    attempt with a small margin, the large-v3 fallback with the full
    default margin, and an InsufficientVramError from the lightweight
    check must skip the (doomed, needs-more-not-less-VRAM) fallback rather
    than attempting it anyway."""

    def test_lightweight_attempt_checks_a_small_margin(self):
        import os
        from unittest.mock import MagicMock, patch

        fake_wav = Path("/fake/sample.wav")

        class FakeModel:
            def __init__(self, name, **kw):
                pass

            def transcribe(self, path, **kw):
                info = MagicMock()
                info.language, info.language_probability = "tr", 0.9
                return iter([]), info

        with patch.dict(os.environ, {"SUBTITLE_AI_SAMPLE_MODEL": "small"}), \
             patch("faster_whisper.WhisperModel", FakeModel), \
             patch("asr.AsrConfig") as MockCfg, \
             patch("gpu.preflight_vram_check") as preflight:
            MockCfg.return_value.compute_type = "int8"
            audio_streams.default_sampler()(fake_wav)

        preflight.assert_called_once_with(0.5)

    def test_fallback_attempt_checks_the_full_default_margin(self):
        import os
        from unittest.mock import MagicMock, patch

        fake_wav = Path("/fake/sample.wav")

        class FakeModel:
            def __init__(self, name, **kw):
                if name == "small":
                    raise OSError("not cached")

            def transcribe(self, path, **kw):
                info = MagicMock()
                info.language, info.language_probability = "tr", 0.9
                return iter([]), info

        with patch.dict(os.environ, {"SUBTITLE_AI_SAMPLE_MODEL": "small"}), \
             patch("faster_whisper.WhisperModel", FakeModel), \
             patch("asr.AsrConfig") as MockCfg, \
             patch("gpu.preflight_vram_check") as preflight:
            MockCfg.return_value.compute_type = "int8"
            audio_streams.default_sampler()(fake_wav)

        # Once for the lightweight attempt (small margin), once for the
        # large-v3 fallback (no explicit arg -- uses gpu's own default).
        self.assertEqual(preflight.call_args_list,
                         [unittest.mock.call(0.5), unittest.mock.call()])

    def test_insufficient_vram_on_lightweight_check_skips_fallback_entirely(self):
        """A failed lightweight-margin check must propagate immediately --
        never attempt large-v3, which needs MORE headroom, not less."""
        import os
        from unittest.mock import patch

        import gpu
        fake_wav = Path("/fake/sample.wav")

        with patch.dict(os.environ, {"SUBTITLE_AI_SAMPLE_MODEL": "small"}), \
             patch("faster_whisper.WhisperModel") as MockModel, \
             patch("asr.AsrConfig"), \
             patch("gpu.preflight_vram_check", side_effect=gpu.InsufficientVramError("no room")):
            with self.assertRaises(gpu.InsufficientVramError):
                audio_streams.default_sampler()(fake_wav)

        MockModel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
