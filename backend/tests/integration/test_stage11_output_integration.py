"""Real ffmpeg burned-in subtitle integration test: builds a tiny synthetic video +
audio, burns rendered SRT cues into it, and verifies the output file is a valid, playable
video of the expected duration — with the source file left untouched."""
import subprocess
from pathlib import Path

import pytest

from app.pipeline.interfaces import TargetCue
from app.pipeline.stage11_qc_output.formatters.burned_in import burn_in_subtitles


def _build_tiny_video(dest: Path, duration_s: float = 3.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=blue:s=160x90:d={duration_s}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}",
         "-shortest", str(dest)],
        capture_output=True, check=True, timeout=30,
    )
    return dest


@pytest.mark.integration
def test_burn_in_produces_valid_video_without_touching_source(tmp_path):
    source = _build_tiny_video(tmp_path / "source.mp4")
    before = source.read_bytes()

    cues = [
        TargetCue(cue_id="c1", start=0.0, end=1.5, text="Hello there", source_chunk_ids=["c1"], char_share_of_chunk=1.0),
        TargetCue(cue_id="c2", start=1.5, end=3.0, text="Goodbye now", source_chunk_ids=["c2"], char_share_of_chunk=1.0),
    ]

    dest = tmp_path / "burned.mp4"
    result_path = burn_in_subtitles(source, cues, dest, use_nvenc=False)

    assert result_path.is_file()
    assert result_path.stat().st_size > 0
    assert source.read_bytes() == before  # source untouched

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(result_path)],
        capture_output=True, text=True, check=True, timeout=15,
    )
    duration = float(probe.stdout.strip())
    assert duration == pytest.approx(3.0, abs=0.5)
