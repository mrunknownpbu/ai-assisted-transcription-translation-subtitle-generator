"""Shared ffmpeg-based audio decoding helpers used by inspection, stream ranking,
language detection, VAD, and ASR. Centralized so every stage decodes a given stream
identically (same resample/channel-down-mix path) instead of drifting independently.

All operations are read-only on the source: ffmpeg is invoked with the source as input
only, output always goes to pipe:1 or an explicit scratch-dir destination, never back
over the source file.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

DEFAULT_SAMPLE_RATE = 16000


class AudioDecodeError(RuntimeError):
    pass


def decode_pcm_bytes(
    path: str | Path, stream_index: int, *, seconds: float | None = None, start_seconds: float = 0.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE, channels: int = 1, timeout: int = 120,
) -> bytes:
    cmd = ["ffmpeg", "-v", "error"]
    if start_seconds > 0:
        # -ss BEFORE -i: fast input-side seeking, needed so sampling a window in the
        # middle of a multi-hour file doesn't require decoding everything before it.
        cmd += ["-ss", str(start_seconds)]
    cmd += ["-i", str(path), "-map", f"0:{stream_index}"]
    if seconds is not None:
        cmd += ["-t", str(seconds)]
    cmd += ["-f", "s16le", "-ac", str(channels), "-ar", str(sample_rate), "pipe:1"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
    except subprocess.CalledProcessError as exc:
        raise AudioDecodeError(f"ffmpeg decode failed for stream {stream_index} of {path}: {exc.stderr}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioDecodeError(f"ffmpeg decode timed out for stream {stream_index} of {path}") from exc
    return out.stdout


def decode_pcm_array(
    path: str | Path, stream_index: int, *, seconds: float | None = None, start_seconds: float = 0.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> np.ndarray:
    raw = decode_pcm_bytes(path, stream_index, seconds=seconds, start_seconds=start_seconds,
                            sample_rate=sample_rate, channels=1)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def extract_wav_file(
    path: str | Path, stream_index: int, dest_path: str | Path, *, sample_rate: int = DEFAULT_SAMPLE_RATE,
    timeout: int = 1800,
) -> Path:
    """Extracts a full-length mono WAV for engines that need a file path (whisper.cpp,
    vosk) rather than an in-memory array. Written under the caller's work_dir scratch
    space, never alongside or over the source."""
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-i", str(path), "-map", f"0:{stream_index}",
        "-ac", "1", "-ar", str(sample_rate),
        str(dest),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
    except subprocess.CalledProcessError as exc:
        raise AudioDecodeError(f"ffmpeg extraction failed for stream {stream_index} of {path}: {exc.stderr}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioDecodeError(f"ffmpeg extraction timed out for stream {stream_index} of {path}") from exc
    return dest
