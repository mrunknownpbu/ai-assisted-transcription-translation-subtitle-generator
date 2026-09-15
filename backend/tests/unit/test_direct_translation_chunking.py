import pytest

from app.pipeline.direct_translation.chunking import group_cues_into_source_chunks
from app.pipeline.stage11_qc_output.formatters.srt import ParsedSrtCue


def _cue(index, start, end, text):
    return ParsedSrtCue(index=index, start=start, end=end, text=text)


@pytest.mark.unit
def test_empty_input_yields_no_chunks():
    assert group_cues_into_source_chunks([], pause_gap_s=1.0) == []


@pytest.mark.unit
def test_single_cue_becomes_one_chunk():
    cues = [_cue(1, 0.0, 1.0, "Hello.")]
    chunks = group_cues_into_source_chunks(cues, pause_gap_s=1.0)
    assert len(chunks) == 1
    assert chunks[0].source_text == "Hello."
    assert chunks[0].word_span == (0.0, 1.0)
    assert chunks[0].source_segment_ids == ["1"]


@pytest.mark.unit
def test_close_cues_merge_into_one_chunk():
    cues = [_cue(1, 0.0, 1.0, "Hello."), _cue(2, 1.3, 2.0, "How are you?")]
    chunks = group_cues_into_source_chunks(cues, pause_gap_s=1.0)
    assert len(chunks) == 1
    assert chunks[0].source_text == "Hello. How are you?"
    assert chunks[0].word_span == (0.0, 2.0)
    assert chunks[0].source_segment_ids == ["1", "2"]


@pytest.mark.unit
def test_a_real_pause_breaks_the_chunk():
    cues = [_cue(1, 0.0, 1.0, "Hello."), _cue(2, 5.0, 6.0, "Later, then.")]
    chunks = group_cues_into_source_chunks(cues, pause_gap_s=1.0)
    assert len(chunks) == 2
    assert chunks[0].source_text == "Hello."
    assert chunks[1].source_text == "Later, then."


@pytest.mark.unit
def test_max_chunk_segments_caps_a_run_of_close_cues():
    cues = [_cue(i, float(i), float(i) + 0.5, f"word{i}") for i in range(5)]
    chunks = group_cues_into_source_chunks(cues, pause_gap_s=1.0, max_chunk_segments=2)
    assert all(len(c.source_segment_ids) <= 2 for c in chunks)
    # nothing dropped
    assert sum(len(c.source_segment_ids) for c in chunks) == 5


@pytest.mark.unit
def test_max_chunk_chars_caps_a_run_of_close_cues():
    cues = [_cue(i, float(i) * 2, float(i) * 2 + 1, "x" * 20) for i in range(5)]
    chunks = group_cues_into_source_chunks(cues, pause_gap_s=1.0, max_chunk_chars=45)
    assert all(len(c.source_text) <= 45 for c in chunks)


@pytest.mark.unit
def test_multiline_cue_text_is_flattened_to_one_line():
    cues = [_cue(1, 0.0, 1.0, "Line one\nLine two")]
    chunks = group_cues_into_source_chunks(cues, pause_gap_s=1.0)
    assert chunks[0].source_text == "Line one Line two"
