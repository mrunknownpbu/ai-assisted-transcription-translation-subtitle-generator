import pytest

from app.evaluation.reference_extraction import (
    ReferenceExtractionError, extract_embedded_subtitle_text, read_sidecar_subtitle_text,
)


@pytest.mark.unit
def test_read_sidecar_subtitle_text_returns_file_contents(tmp_path):
    srt = tmp_path / "ref.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello\n")
    assert "Hello" in read_sidecar_subtitle_text(srt)


@pytest.mark.unit
def test_read_sidecar_subtitle_text_strips_bom(tmp_path):
    srt = tmp_path / "ref.srt"
    srt.write_bytes("﻿1\n00:00:00,000 --> 00:00:02,000\nHello\n".encode("utf-8"))
    text = read_sidecar_subtitle_text(srt)
    assert not text.startswith("﻿")


@pytest.mark.unit
def test_read_sidecar_subtitle_text_missing_file_raises():
    with pytest.raises(ReferenceExtractionError):
        read_sidecar_subtitle_text("/no/such/file.srt")


@pytest.mark.unit
def test_extract_embedded_subtitle_text_missing_source_raises():
    with pytest.raises(ReferenceExtractionError):
        extract_embedded_subtitle_text("/no/such/video.mkv", 0, timeout=10)
