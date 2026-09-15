import pytest

from app.pipeline.interfaces import TargetCue
from app.pipeline.stage11_qc_output import qc_output
from app.pipeline.stage11_qc_output.formatters.srt import render_srt


def _write(tmp_path, cues, name="out.srt"):
    path = tmp_path / name
    path.write_text(render_srt(cues), encoding="utf-8")
    return path


@pytest.mark.unit
def test_valid_output_file_passes(tmp_path):
    cues = [
        TargetCue(cue_id="c1", start=0.0, end=1.5, text="Hello", source_chunk_ids=["c1"], char_share_of_chunk=1.0),
        TargetCue(cue_id="c2", start=1.5, end=3.0, text="World", source_chunk_ids=["c2"], char_share_of_chunk=1.0),
    ]
    path = _write(tmp_path, cues)

    report = qc_output.run(path)

    assert report.passed is True


@pytest.mark.unit
def test_unparseable_file_fails_gracefully():
    report = qc_output.run("/nonexistent/path/does-not-exist.srt")
    assert report.passed is False
    assert any(f.check == "output_file_parses" and not f.passed for f in report.findings)


@pytest.mark.unit
def test_overlapping_cues_in_the_written_file_are_caught(tmp_path):
    # Hand-construct overlapping SRT content directly, bypassing the renderer, so this
    # test exercises the independent re-parse/validate path even if the renderer itself
    # would never produce this.
    content = (
        "1\n00:00:00,000 --> 00:00:03,000\nHello\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nWorld\n"
    )
    path = tmp_path / "overlap.srt"
    path.write_text(content, encoding="utf-8")

    report = qc_output.run(path)

    assert report.passed is False
    assert any(f.check == "output_cue_no_overlap" and not f.passed for f in report.findings)
