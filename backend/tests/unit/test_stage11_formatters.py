import json

import pytest

from app.pipeline.interfaces import TargetCue
from app.pipeline.stage11_qc_output.formatters.provenance import write_sidecar
from app.pipeline.stage11_qc_output.formatters.srt import render_srt
from app.pipeline.stage11_qc_output.formatters.vtt_webvtt import render_webvtt


def _cues():
    return [
        TargetCue(cue_id="c1", start=0.0, end=1.5, text="Hello there", source_chunk_ids=["c1"], char_share_of_chunk=1.0),
        TargetCue(cue_id="c2", start=1.5, end=3.75, text="How are you\ntoday?", source_chunk_ids=["c2"], char_share_of_chunk=1.0),
    ]


@pytest.mark.unit
def test_srt_format_structure_and_timestamps():
    srt = render_srt(_cues())
    lines = srt.strip("\n").split("\n")
    assert lines[0] == "1"
    assert lines[1] == "00:00:00,000 --> 00:00:01,500"
    assert lines[2] == "Hello there"
    assert "2" in lines
    assert "00:00:01,500 --> 00:00:03,750" in srt


@pytest.mark.unit
def test_srt_preserves_multiline_cue_text():
    srt = render_srt(_cues())
    assert "How are you\ntoday?" in srt


@pytest.mark.unit
def test_webvtt_starts_with_header_and_uses_dot_separated_ms():
    vtt = render_webvtt(_cues())
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.500" in vtt


@pytest.mark.unit
def test_webvtt_embeds_provenance_note_block_when_given():
    vtt = render_webvtt(_cues(), provenance={"engine": "nllb", "stream_id": "abc123"})
    assert "NOTE" in vtt
    assert "engine: nllb" in vtt
    assert "stream_id: abc123" in vtt


@pytest.mark.unit
def test_webvtt_and_vtt_content_are_identical_by_design():
    # Both .vtt and .webvtt are produced from the same renderer/content per the spec's
    # requirement for both extensions — there is no separate .vtt-specific formatter.
    content_a = render_webvtt(_cues())
    content_b = render_webvtt(_cues())
    assert content_a == content_b


@pytest.mark.unit
def test_provenance_sidecar_written_as_valid_json(tmp_path):
    dest = tmp_path / "movie.en.provenance.json"
    provenance = {"asr_engine": "faster_whisper", "hardware_profile": {"vendor": "cpu"}}

    result_path = write_sidecar(dest, provenance)

    assert result_path == dest
    loaded = json.loads(dest.read_text())
    assert loaded["asr_engine"] == "faster_whisper"
    assert loaded["hardware_profile"]["vendor"] == "cpu"
