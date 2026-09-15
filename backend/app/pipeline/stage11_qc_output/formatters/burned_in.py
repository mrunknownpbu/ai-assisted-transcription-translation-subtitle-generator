"""Burns rendered SRT text into a video via ffmpeg's `subtitles` filter. This re-encodes
the video stream (the filter operates on decoded frames), so the audio stream is always
copied untouched rather than re-encoded. NVENC is used when the hardware profile reports
an NVIDIA GPU; otherwise falls back to libx264 on CPU. Output always goes to a new file
in the output volume — the source video is never opened for writing.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from app.pipeline.stage11_qc_output.formatters.srt import render_srt


class BurnInError(RuntimeError):
    pass


def _escape_subtitles_path(path: Path) -> str:
    # ffmpeg's filtergraph parser treats ':' and other special chars specially; escaping
    # per ffmpeg's own documented convention for the subtitles filter's path argument.
    escaped = str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return escaped


def burn_in_subtitles(
    source_video_path: str | Path, cues, dest_path: str | Path, *, use_nvenc: bool = False, timeout: int = 3600,
) -> Path:
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    video_codec = "h264_nvenc" if use_nvenc else "libx264"

    with tempfile.NamedTemporaryFile("w", suffix=".srt", delete=False, encoding="utf-8") as tmp_srt:
        tmp_srt.write(render_srt(cues))
        tmp_srt_path = Path(tmp_srt.name)

    try:
        vf = f"subtitles='{_escape_subtitles_path(tmp_srt_path)}'"
        cmd = [
            "ffmpeg", "-v", "error", "-y",
            "-i", str(source_video_path),
            "-vf", vf,
            "-c:v", video_codec,
            "-c:a", "copy",
            str(dest),
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)
        except subprocess.CalledProcessError as exc:
            if use_nvenc:
                # NVENC can fail for reasons unrelated to whether a GPU exists (session
                # limits, driver mismatch); fall back to libx264 once rather than failing
                # the whole job over an encoder-specific issue.
                cmd[cmd.index("h264_nvenc")] = "libx264"
                try:
                    subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)
                except subprocess.CalledProcessError as exc2:
                    raise BurnInError(f"ffmpeg burn-in failed (NVENC and libx264 both failed): {exc2.stderr}") from exc2
            else:
                raise BurnInError(f"ffmpeg burn-in failed: {exc.stderr}") from exc
        except subprocess.TimeoutExpired as exc:
            raise BurnInError(f"ffmpeg burn-in timed out after {timeout}s") from exc
    finally:
        tmp_srt_path.unlink(missing_ok=True)

    return dest
