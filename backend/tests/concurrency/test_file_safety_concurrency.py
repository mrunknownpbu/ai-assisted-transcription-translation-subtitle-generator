from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from app.pipeline.stage1_inspection.inspector import inspect_media
from tests.fixtures.synthetic_media import build_multi_stream_fixture


@pytest.mark.concurrency
def test_concurrent_inspection_of_same_file_is_safe_and_consistent(ffmpeg_fixture_dir):
    """Multiple jobs might reference the same source file concurrently (e.g. two jobs
    targeting different languages from one upload). Inspection must be read-only and
    produce byte-identical results no matter how many threads read it at once."""
    media_path = build_multi_stream_fixture(ffmpeg_fixture_dir)
    before = media_path.read_bytes()

    def run_inspection():
        result = inspect_media(media_path)
        return result.file_hash, tuple(sorted(s.stream_hash for s in result.audio_streams))

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(run_inspection) for _ in range(6)]
        results = [f.result() for f in as_completed(futures)]

    assert len(set(results)) == 1  # every concurrent read produced identical hashes
    assert media_path.read_bytes() == before  # source file was never modified
