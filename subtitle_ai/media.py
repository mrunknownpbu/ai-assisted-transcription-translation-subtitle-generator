"""Media inspection and audio extraction.

Security: every subprocess call here uses an argument array, never a
shell string -- a filename containing `; rm -rf /` or backticks is just
an opaque argv element to subprocess, not something a shell ever
interprets. No f-string or `.format()` is ever used to build a command
line; only list concatenation.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


class MediaError(RuntimeError):
    pass


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".webm"}


def probe(video_path: str | Path) -> tuple[list[dict], dict]:
    """(streams, format) from one ffprobe call -- every field ffprobe
    knows about each stream, unfiltered, so a typed view (see
    audio_streams.py) can be built on top without a second invocation or
    a narrower `-show_entries` list silently dropping a field later code
    turns out to need."""
    command = ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
              "-show_format", str(video_path)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise MediaError(f"ffprobe failed: {exc}") from exc
    data = json.loads(result.stdout)
    return data.get("streams", []), data.get("format", {})


def probe_streams(video_path: str | Path) -> list[dict]:
    return probe(video_path)[0]


def pick_audio_stream(streams: list[dict], preferred_language: str | None = None) -> dict:
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio:
        raise MediaError("no audio stream found")
    if preferred_language:
        matches = [s for s in audio if (s.get("tags", {}).get("language") or "").startswith(preferred_language)]
        if matches:
            return matches[0]
    defaults = [s for s in audio if s.get("disposition", {}).get("default")]
    return defaults[0] if defaults else audio[0]


def extract_audio(video_path: str | Path, stream_index: int, out_wav: str | Path,
                  sample_rate: int = 16000) -> Path:
    """16 kHz mono PCM WAV -- what faster-whisper/whisperx expect. Reads
    the video file only; never touches any existing subtitle."""
    out = Path(out_wav)
    out.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-map", f"0:{stream_index}", "-ac", "1", "-ar", str(sample_rate),
        "-vn", "-sn", str(out),
    ]
    try:
        subprocess.run(command, capture_output=True, text=True, timeout=1800, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise MediaError(f"ffmpeg extraction failed: {exc}") from exc
    return out


def content_fingerprint(video_path: str | Path, *, sample_bytes: int = 1_048_576) -> str:
    """Cheap, stable fingerprint for cache keys -- full-file hashing of a
    multi-GB video on every job would be wasteful; head+size is enough to
    detect "this is a different or re-encoded file", not to be
    cryptographically exhaustive."""
    path = Path(video_path)
    size = path.stat().st_size
    h = hashlib.sha256()
    h.update(str(size).encode())
    with open(path, "rb") as fh:
        h.update(fh.read(sample_bytes))
    return h.hexdigest()
