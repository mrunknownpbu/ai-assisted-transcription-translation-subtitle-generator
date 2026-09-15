"""Generates small, license-free synthetic media for tests, since real dialogue media
can't be shipped in the repo. Speech comes from espeak-ng (offline TTS); non-dialogue
comes from ffmpeg's lavfi sine/anullsrc generators. Everything here is throwaway test
fixture generation, not part of the product pipeline.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

SAMPLE_RATE = 16000


def generate_speech_wav(text: str, dest_path: Path, voice: str = "en", speed_wpm: int = 150) -> Path:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["espeak-ng", "-v", voice, "-s", str(speed_wpm), "-w", str(dest_path), text],
        capture_output=True, check=True, timeout=30,
    )
    return dest_path


def generate_tone_wav(dest_path: Path, freq: int = 440, duration_s: float = 8.0) -> Path:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration={duration_s}:sample_rate={SAMPLE_RATE}",
         str(dest_path)],
        capture_output=True, check=True, timeout=30,
    )
    return dest_path


def generate_silence_wav(dest_path: Path, duration_s: float = 5.0) -> Path:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono", "-t", str(duration_s),
         str(dest_path)],
        capture_output=True, check=True, timeout=30,
    )
    return dest_path


def mux_multi_stream(dest_path: Path, wav_paths: list[Path], container_ext: str = "mkv") -> Path:
    """Combines N mono WAV files into one container with N audio streams (stream index
    order matches wav_paths order), simulating a real multi-audio-track media file."""
    dest_path = dest_path.with_suffix(f".{container_ext}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for wav in wav_paths:
        cmd += ["-i", str(wav)]
    for i in range(len(wav_paths)):
        cmd += ["-map", f"{i}:a"]
    cmd += [str(dest_path)]
    subprocess.run(cmd, capture_output=True, check=True, timeout=60)
    return dest_path


def build_multi_stream_fixture(tmp_dir: Path, speech_text: str = "This is a test of the subtitle platform.") -> Path:
    """Builds an .mkv with 3 audio streams: [0] pure tone (non-dialogue), [1] synthetic
    speech (dialogue), [2] silence. Stream 1 should always out-rank 0 and 2 in Stage 2."""
    tone = generate_tone_wav(tmp_dir / "tone.wav")
    speech = generate_speech_wav(speech_text, tmp_dir / "speech.wav")
    silence = generate_silence_wav(tmp_dir / "silence.wav")
    return mux_multi_stream(tmp_dir / "multi_stream", [tone, speech, silence])


def build_single_speech_fixture(tmp_dir: Path, speech_text: str = "Hello world, this is a spoken test sentence.") -> Path:
    speech = generate_speech_wav(speech_text, tmp_dir / "speech_only.wav")
    return mux_multi_stream(tmp_dir / "single_speech", [speech])
