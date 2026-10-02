"""subtitle_ai/turns.py: heuristic and voice-based speaker-turn detection.
Voice-detector tests inject a fake onnxruntime-shaped session (no real
model/network needed) and use synthetic tones instead of real speech."""

from __future__ import annotations

import math
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

import turns
from transcript import Word


def w(text: str, start: float, end: float) -> Word:
    return Word(text=text, original_text=text, start=start, end=end, probability=0.9)


class HeuristicTurnsTests(unittest.TestCase):
    def test_long_pause_after_sentence_end_is_a_turn(self):
        words = [w("Tamam.", 0.0, 0.5), w("Evet", 1.5, 1.8), w("öyle.", 1.8, 2.1)]
        self.assertEqual(turns.heuristic_turns(words), {1})

    def test_no_pause_and_a_long_reply_is_not_a_turn(self):
        # A reply longer than SHORT_REPLY_WORDS with no pause and no
        # question mark has no signal at all -- must not be flagged.
        words = ([w("Tamam.", 0.0, 0.5)]
                + [w(t, 0.55 + i * 0.2, 0.7 + i * 0.2)
                   for i, t in enumerate("bu işi böyle bitirmemiz gerekiyor bugün.".split())])
        self.assertEqual(turns.heuristic_turns(words), set())

    def test_question_implies_a_reply_given_at_least_a_small_pause(self):
        words = [w("Geliyor", 0.0, 0.4), w("musun?", 0.4, 0.6), w("Evet.", 0.85, 1.05)]
        self.assertEqual(turns.heuristic_turns(words), {2})

    def test_question_with_no_pause_at_all_is_not_a_turn(self):
        # Same speaker asking and immediately answering their own rhetorical
        # question -- zero gap must not be treated as a reply.
        words = [w("Geliyor", 0.0, 0.4), w("musun?", 0.4, 0.6), w("diye", 0.6, 0.8), w("sordu.", 0.8, 1.0)]
        self.assertEqual(turns.heuristic_turns(words), set())

    def test_short_reply_after_a_statement_is_a_turn(self):
        words = [w("Bugün", 0.0, 0.3), w("hava", 0.3, 0.6), w("çok", 0.6, 0.8), w("güzel.", 0.8, 1.1),
                 w("Evet.", 1.3, 1.5)]
        self.assertEqual(turns.heuristic_turns(words), {4})

    def test_long_continuation_with_no_pause_or_question_is_not_a_turn(self):
        words = ("Bu sabah çok erken kalktım çünkü işe gitmem gerekiyordu ve otobüsü "
                "kaçırmak istemiyordum bugün.").split()
        ws = [w(t if i < len(words) - 1 else t, i * 0.2, i * 0.2 + 0.15) for i, t in enumerate(words)]
        self.assertEqual(turns.heuristic_turns(ws), set())

    def test_final_word_of_transcript_never_registers_a_turn(self):
        words = [w("Tamam.", 0.0, 0.5)]
        self.assertEqual(turns.heuristic_turns(words), set())


class FbankTests(unittest.TestCase):
    def test_shape_and_silence_is_low_energy(self):
        samples = np.zeros(16000, dtype=np.float32)
        feats = turns.extract_fbank(samples)
        self.assertEqual(feats.shape[1], 80)
        self.assertGreater(feats.shape[0], 50)

    def test_too_short_audio_returns_empty(self):
        feats = turns.extract_fbank(np.zeros(10, dtype=np.float32))
        self.assertEqual(feats.shape, (0, 80))

    def test_sine_tone_produces_nonzero_varying_features(self):
        t = np.arange(16000) / 16000.0
        samples = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        feats = turns.extract_fbank(samples)
        self.assertFalse(np.allclose(feats, feats[0]))  # not degenerate/constant


class _FakeSession:
    """Stands in for onnxruntime.InferenceSession: returns a fixed
    embedding per distinguishable input so voice_turns' similarity logic
    is testable without a real model. Keyed by mean absolute feature
    value, rounded, so two different synthetic tones map to two
    different fake embeddings."""

    def run(self, output_names, inputs):
        feats = inputs["feats"]
        key = round(float(np.abs(feats).mean()), 3)
        rng = np.random.RandomState(int(key * 1000) % (2**31))
        return [rng.rand(1, 256).astype(np.float32)]


def _write_wav(path: Path, samples: np.ndarray, sample_rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes((samples * 32767).astype(np.int16).tobytes())


class VoiceTurnsTests(unittest.TestCase):
    def test_different_tones_register_a_turn(self):
        sr = 16000
        t = np.arange(sr * 4) / sr
        # Two very different synthetic "speakers" (different pitch), each
        # a full sentence-length chunk (>= MIN_CHUNK_SECONDS).
        speaker_a = 0.3 * np.sin(2 * math.pi * 200 * t[:sr * 2])
        speaker_b = 0.3 * np.sin(2 * math.pi * 200 * t[:sr * 2]) * 0  # silence-ish, distinct mean energy
        speaker_b = 0.3 * np.sin(2 * math.pi * 3000 * t[:sr * 2])
        samples = np.concatenate([speaker_a, speaker_b]).astype(np.float32)
        words = [w("Merhaba", 0.0, 1.9), w("nasılsın?", 1.9, 2.0), w("İyiyim", 2.1, 3.9), w("sağ ol.", 3.9, 4.0)]
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "audio.wav"
            _write_wav(wav_path, samples, sr)
            result = turns.voice_turns(words, str(wav_path), session=_FakeSession(), similarity_threshold=2.0)
            # threshold=2.0 is unreachable (cosine sim is always <=1), so
            # every chunk-to-chunk comparison after the first must count
            # as a turn -- confirms the wiring (spans -> embeddings ->
            # comparison -> turn) works end to end.
            self.assertEqual(result, {2})

    def test_short_chunk_is_skipped(self):
        sr = 16000
        samples = np.zeros(sr, dtype=np.float32)
        words = [w("Aa!", 0.0, 0.2), w("Sonra", 0.5, 1.5), w("konuşuruz.", 1.5, 2.0)]
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "audio.wav"
            _write_wav(wav_path, samples, sr)
            result = turns.voice_turns(words, str(wav_path), session=_FakeSession())
            self.assertEqual(result, set())  # "Aa!" alone is under MIN_CHUNK_SECONDS


class DetectTurnsDispatchTests(unittest.TestCase):
    def test_off_mode_returns_empty(self):
        self.assertEqual(turns.detect_turns([w("Tamam.", 0.0, 0.5)], mode="off"), set())

    def test_voice_mode_without_wav_path_falls_back_to_heuristic(self):
        words = [w("Tamam.", 0.0, 0.5), w("Evet", 1.5, 1.8), w("öyle.", 1.8, 2.1)]
        self.assertEqual(turns.detect_turns(words, wav_path=None, mode="voice"),
                         turns.heuristic_turns(words))

    def test_mode_from_environment(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"SUBTITLE_AI_TURN_DETECTION": "off"}):
            self.assertEqual(turns.turn_detection_mode(), "off")
        with patch.dict(os.environ, {"SUBTITLE_AI_TURN_DETECTION": "bogus"}):
            self.assertEqual(turns.turn_detection_mode(), turns.DEFAULT_MODE)


if __name__ == "__main__":
    unittest.main()
