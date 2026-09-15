import pytest

from app.pipeline.stage1_inspection.inspector import inspect_media
from tests.fixtures.synthetic_media import build_multi_stream_fixture


@pytest.mark.unit
def test_inspect_media_enumerates_all_audio_streams(ffmpeg_fixture_dir):
    media_path = build_multi_stream_fixture(ffmpeg_fixture_dir)

    result = inspect_media(media_path)

    assert len(result.audio_streams) == 3
    assert result.file_hash and len(result.file_hash) == 64
    assert result.container_format  # ffprobe always reports something (e.g. "matroska,webm")
    for stream in result.audio_streams:
        assert stream.stream_hash and stream.stream_hash != "unavailable"
        assert stream.sample_rate > 0


@pytest.mark.unit
def test_stream_hash_is_content_derived_not_filename_derived(ffmpeg_fixture_dir, tmp_path):
    media_path = build_multi_stream_fixture(ffmpeg_fixture_dir)
    result_a = inspect_media(media_path)

    renamed = tmp_path / "totally_different_name.mkv"
    renamed.write_bytes(media_path.read_bytes())
    result_b = inspect_media(renamed)

    hashes_a = sorted(s.stream_hash for s in result_a.audio_streams)
    hashes_b = sorted(s.stream_hash for s in result_b.audio_streams)
    assert hashes_a == hashes_b


@pytest.mark.unit
def test_inspection_never_modifies_source_file(ffmpeg_fixture_dir):
    media_path = build_multi_stream_fixture(ffmpeg_fixture_dir)
    before = media_path.read_bytes()

    inspect_media(media_path)

    after = media_path.read_bytes()
    assert before == after
