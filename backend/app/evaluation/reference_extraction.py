"""Pulls a real reference subtitle track for accuracy evaluation from either an embedded
mkv subtitle stream or an external sidecar .srt file already present in the library --
never generated, always something a human actually produced for this exact episode."""
from __future__ import annotations

import subprocess
from pathlib import Path


class ReferenceExtractionError(RuntimeError):
    pass


def extract_embedded_subtitle_text(path: str | Path, stream_index: int, *, timeout: int = 60) -> str:
    """Copies (not re-encodes) an embedded text-based subtitle stream out to SRT. Read-only
    on the source, same convention as core/audio.py's decode helpers."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:{stream_index}", "-c:s", "srt", "-f", "srt", "pipe:1"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
    except subprocess.CalledProcessError as exc:
        raise ReferenceExtractionError(
            f"Failed to extract subtitle stream {stream_index} from {path}: {exc.stderr}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ReferenceExtractionError(f"Timed out extracting subtitle stream {stream_index} from {path}") from exc
    text = out.stdout.decode("utf-8", errors="replace")
    if not text.strip():
        raise ReferenceExtractionError(f"Stream {stream_index} of {path} produced no subtitle text (wrong index?)")
    return text


def read_sidecar_subtitle_text(path: str | Path) -> str:
    path = Path(path)
    if not path.is_file():
        raise ReferenceExtractionError(f"Reference subtitle file not found: {path}")
    return path.read_text(encoding="utf-8-sig", errors="replace")
