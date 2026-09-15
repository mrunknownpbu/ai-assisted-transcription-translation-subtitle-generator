import pytest

from app.pipeline.interfaces import TargetCue
from app.pipeline.stage11_qc_output.formatters.srt import parse_srt, render_srt


@pytest.mark.unit
def test_parse_matches_render_round_trip():
    cues = [
        TargetCue(cue_id="c1", start=0.0, end=1.5, text="Hello there", source_chunk_ids=["c1"], char_share_of_chunk=1.0),
        TargetCue(cue_id="c2", start=1.5, end=3.75, text="How are you\ntoday?", source_chunk_ids=["c2"], char_share_of_chunk=1.0),
    ]
    content = render_srt(cues)

    parsed = parse_srt(content)

    assert len(parsed) == 2
    assert parsed[0].start == pytest.approx(0.0)
    assert parsed[0].end == pytest.approx(1.5)
    assert parsed[0].text == "Hello there"
    assert parsed[1].text == "How are you\ntoday?"


@pytest.mark.unit
def test_parse_strips_leading_bom():
    content = "﻿1\n00:00:00,000 --> 00:00:01,000\nHello\n"
    parsed = parse_srt(content)
    assert len(parsed) == 1
    assert parsed[0].text == "Hello"


@pytest.mark.unit
def test_parse_accepts_dot_decimal_separator():
    content = "1\n00:00:00.000 --> 00:00:01.500\nHello\n"
    parsed = parse_srt(content)
    assert parsed[0].end == pytest.approx(1.5)


@pytest.mark.unit
def test_parse_empty_content_returns_no_cues():
    assert parse_srt("") == []
