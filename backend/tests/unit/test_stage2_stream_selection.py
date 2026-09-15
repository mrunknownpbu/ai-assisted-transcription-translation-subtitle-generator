import pytest

from app.pipeline.stage1_inspection.inspector import inspect_media
from app.pipeline.stage2_stream_selection.ranker import apply_manual_override, rank_streams
from tests.fixtures.synthetic_media import build_multi_stream_fixture


@pytest.mark.unit
def test_speech_stream_outranks_tone_and_silence(ffmpeg_fixture_dir):
    media_path = build_multi_stream_fixture(ffmpeg_fixture_dir)
    inspection = inspect_media(media_path)

    result = rank_streams(media_path, inspection.audio_streams)

    assert result.selected_by == "auto"
    assert result.selected_stream_index == 1  # speech stream, by construction order
    scores_by_index = {r.stream_index: r.dialogue_score for r in result.rankings}
    assert scores_by_index[1] > scores_by_index[0]  # speech > tone
    assert scores_by_index[1] > scores_by_index[2]  # speech > silence


@pytest.mark.unit
def test_rankings_sorted_best_first(ffmpeg_fixture_dir):
    media_path = build_multi_stream_fixture(ffmpeg_fixture_dir)
    inspection = inspect_media(media_path)

    result = rank_streams(media_path, inspection.audio_streams)

    scores = [r.dialogue_score for r in result.rankings]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.unit
def test_manual_override_replaces_auto_selection(ffmpeg_fixture_dir):
    media_path = build_multi_stream_fixture(ffmpeg_fixture_dir)
    inspection = inspect_media(media_path)
    auto_result = rank_streams(media_path, inspection.audio_streams)

    overridden = apply_manual_override(auto_result, stream_index=2)

    assert overridden.selected_by == "manual"
    assert overridden.selected_stream_index == 2
    assert overridden.rankings == auto_result.rankings  # rankings themselves are untouched


@pytest.mark.unit
def test_manual_override_rejects_unknown_stream_index(ffmpeg_fixture_dir):
    media_path = build_multi_stream_fixture(ffmpeg_fixture_dir)
    inspection = inspect_media(media_path)
    auto_result = rank_streams(media_path, inspection.audio_streams)

    with pytest.raises(ValueError):
        apply_manual_override(auto_result, stream_index=99)
