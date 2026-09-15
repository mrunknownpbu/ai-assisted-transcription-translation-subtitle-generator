import json

import pytest

from app.evaluation.transcript_cues import canonical_transcript_to_cues


def _write_transcript(tmp_path, segments):
    path = tmp_path / "canonical_transcript.json"
    path.write_text(json.dumps({"segments": segments, "provenance": {}}))
    return path


@pytest.mark.unit
def test_converts_kept_segments_to_cues(tmp_path):
    path = _write_transcript(tmp_path, [
        {"start": 0.0, "end": 1.0, "text": "Hello", "suppressed": False},
        {"start": 1.0, "end": 2.0, "text": "World", "suppressed": False},
    ])
    cues = canonical_transcript_to_cues(path)
    assert [c.text for c in cues] == ["Hello", "World"]
    assert cues[0].start == 0.0 and cues[0].end == 1.0


@pytest.mark.unit
def test_excludes_suppressed_segments(tmp_path):
    path = _write_transcript(tmp_path, [
        {"start": 0.0, "end": 1.0, "text": "Real dialogue", "suppressed": False},
        {"start": 1.0, "end": 2.0, "text": "Hallucinated credit", "suppressed": True},
    ])
    cues = canonical_transcript_to_cues(path)
    assert [c.text for c in cues] == ["Real dialogue"]


@pytest.mark.unit
def test_excludes_empty_text_segments(tmp_path):
    path = _write_transcript(tmp_path, [
        {"start": 0.0, "end": 1.0, "text": "  ", "suppressed": False},
        {"start": 1.0, "end": 2.0, "text": "Real", "suppressed": False},
    ])
    cues = canonical_transcript_to_cues(path)
    assert [c.text for c in cues] == ["Real"]


@pytest.mark.unit
def test_empty_segment_list_produces_no_cues(tmp_path):
    path = _write_transcript(tmp_path, [])
    assert canonical_transcript_to_cues(path) == []
