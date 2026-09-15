import pytest

from app.pipeline.interfaces import SourceChunk, TargetCueDraft
from app.pipeline.stage10_timing_projection.projector import project_timing


def _chunk(chunk_id, start, end):
    return SourceChunk(chunk_id=chunk_id, source_text="x", word_span=(start, end), source_segment_ids=["s"])


@pytest.mark.unit
def test_full_share_draft_gets_exact_chunk_span():
    chunk = _chunk("c1", 10.0, 12.0)
    draft = TargetCueDraft(draft_id="c1_d0", text="hi", source_chunk_ids=["c1"], char_share_of_chunks={"c1": 1.0})

    cues = project_timing([draft], [chunk], min_cue_duration_s=0.5)

    assert cues[0].start == pytest.approx(10.0)
    assert cues[0].end == pytest.approx(12.0)


@pytest.mark.unit
def test_split_chunk_drafts_are_contiguous_and_proportional():
    chunk = _chunk("c1", 0.0, 10.0)
    d1 = TargetCueDraft(draft_id="c1_d0", text="a", source_chunk_ids=["c1"], char_share_of_chunks={"c1": 0.4})
    d2 = TargetCueDraft(draft_id="c1_d1", text="b", source_chunk_ids=["c1"], char_share_of_chunks={"c1": 0.6})

    cues = project_timing([d1, d2], [chunk], min_cue_duration_s=0.1)

    assert cues[0].start == pytest.approx(0.0)
    assert cues[0].end == pytest.approx(4.0)
    assert cues[1].start == pytest.approx(4.0)  # contiguous — no gap invented, no overlap
    assert cues[1].end == pytest.approx(10.0)


@pytest.mark.unit
def test_merged_chunks_span_the_union_of_both():
    chunk_a = _chunk("c1", 0.0, 1.0)
    chunk_b = _chunk("c2", 1.3, 2.0)
    draft = TargetCueDraft(draft_id="merged", text="hi there", source_chunk_ids=["c1", "c2"],
                            char_share_of_chunks={"c1": 1.0, "c2": 1.0})

    cues = project_timing([draft], [chunk_a, chunk_b], min_cue_duration_s=0.1)

    assert cues[0].start == pytest.approx(0.0)
    assert cues[0].end == pytest.approx(2.0)


@pytest.mark.unit
def test_minimum_duration_is_enforced_when_room_available():
    chunk = _chunk("c1", 0.0, 0.2)  # naturally only 0.2s
    draft = TargetCueDraft(draft_id="c1_d0", text="hi", source_chunk_ids=["c1"], char_share_of_chunks={"c1": 1.0})

    cues = project_timing([draft], [chunk], min_cue_duration_s=0.8)

    assert cues[0].end - cues[0].start == pytest.approx(0.8)


@pytest.mark.unit
def test_minimum_duration_never_overlaps_next_cue():
    chunk_a = _chunk("c1", 0.0, 0.3)
    chunk_b = _chunk("c2", 0.5, 1.0)
    d1 = TargetCueDraft(draft_id="d1", text="a", source_chunk_ids=["c1"], char_share_of_chunks={"c1": 1.0})
    d2 = TargetCueDraft(draft_id="d2", text="b", source_chunk_ids=["c2"], char_share_of_chunks={"c2": 1.0})

    cues = project_timing([d1, d2], [chunk_a, chunk_b], min_cue_duration_s=0.8)

    assert cues[0].end <= cues[1].start  # extension never creates an overlap
