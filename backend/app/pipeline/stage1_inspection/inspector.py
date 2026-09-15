"""Stage 1: Media Inspection.

Enumerates every audio stream in a media file via ffprobe, independent of container
format (mp4/mkv/mov/... — ffprobe abstracts this) and independent of how many streams
exist. Also computes a content-derived hash per audio stream so caching downstream
(language detection, ASR) is keyed to the actual audio, not the filename or container.
Read-only: never writes to, moves, or transcodes the source file.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path

from app.core.audio import AudioDecodeError, decode_pcm_bytes
from app.pipeline.interfaces import AudioStreamInfo, MediaInspectionResult

logger = logging.getLogger("subtitle_platform.pipeline.inspection")

FFPROBE_TIMEOUT_S = 60
CONTENT_SAMPLE_SECONDS = 8  # how much decoded audio we hash to fingerprint stream identity


class InspectionError(RuntimeError):
    pass


def _run_ffprobe(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT_S, check=True)
    except subprocess.CalledProcessError as exc:
        raise InspectionError(f"ffprobe failed on {path}: {exc.stderr}") from exc
    except subprocess.TimeoutExpired as exc:
        raise InspectionError(f"ffprobe timed out on {path} after {FFPROBE_TIMEOUT_S}s") from exc
    return json.loads(out.stdout)


def _sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def _content_sample_hash(path: Path, stream_index: int) -> str:
    """Decode a short PCM sample of this exact audio stream and hash it. This is what
    ties cache identity to the actual audio rather than to file metadata: re-muxing the
    same audio into a different container, or renaming the file, does not change this
    hash; replacing the audio track does."""
    try:
        raw = decode_pcm_bytes(path, stream_index, seconds=CONTENT_SAMPLE_SECONDS)
    except AudioDecodeError as exc:
        logger.warning("Content-sample hash decode failed for stream %d of %s: %s", stream_index, path, exc)
        return "unavailable"
    return hashlib.sha256(raw).hexdigest()


def inspect_media(source_path: str | Path) -> MediaInspectionResult:
    path = Path(source_path)
    if not path.is_file():
        raise InspectionError(f"Not a file: {path}")

    probe = _run_ffprobe(path)
    fmt = probe.get("format", {})
    streams = probe.get("streams", [])

    audio_streams: list[AudioStreamInfo] = []
    subtitle_streams: list[dict] = []

    for s in streams:
        codec_type = s.get("codec_type")
        idx = s.get("index")
        if codec_type == "audio":
            duration_s = float(s.get("duration") or fmt.get("duration") or 0.0)
            content_hash = _content_sample_hash(path, idx)
            stream_hash_input = (
                f"{s.get('codec_name')}|{s.get('channels')}|{s.get('sample_rate')}|"
                f"{duration_s:.3f}|{content_hash}"
            )
            stream_hash = hashlib.sha256(stream_hash_input.encode()).hexdigest()
            tags = s.get("tags", {}) or {}
            audio_streams.append(AudioStreamInfo(
                stream_index=idx,
                codec=s.get("codec_name", "unknown"),
                channels=int(s.get("channels") or 0),
                sample_rate=int(s.get("sample_rate") or 0),
                duration_s=duration_s,
                stream_hash=stream_hash,
                embedded_language_tag=tags.get("language"),  # evidence only
                bit_rate=int(s["bit_rate"]) if s.get("bit_rate") else None,
                channel_layout=s.get("channel_layout"),
            ))
        elif codec_type == "subtitle":
            # Recorded as evidence only; Stage 4 ASR must never read these as transcription input.
            subtitle_streams.append({
                "stream_index": idx,
                "codec": s.get("codec_name"),
                "language_tag": (s.get("tags", {}) or {}).get("language"),
                "title": (s.get("tags", {}) or {}).get("title"),
            })

    if not audio_streams:
        logger.warning("No audio streams found in %s — job cannot proceed past inspection.", path)

    return MediaInspectionResult(
        source_path=str(path),
        filename=path.name,
        container_format=fmt.get("format_name", "unknown"),
        size_bytes=int(fmt.get("size") or path.stat().st_size),
        file_hash=_sha256_file(path),
        duration_s=float(fmt.get("duration") or 0.0),
        audio_streams=audio_streams,
        raw_ffprobe_json=probe,
        existing_subtitle_streams=subtitle_streams,
    )
