import json

import pytest

from app.evaluation.series_cache import load_series_cache, record_episode_result


@pytest.mark.unit
def test_missing_cache_file_yields_empty_skeleton(tmp_path):
    cache = load_series_cache(tmp_path, tvdb_id=383383)
    assert cache == {"tvdb_id": 383383, "episodes": {}}


@pytest.mark.unit
def test_record_then_load_round_trips(tmp_path):
    record_episode_result(tmp_path, tvdb_id=383383, episode=1, result={"wer": 0.12, "job_id": "abc"})
    cache = load_series_cache(tmp_path, tvdb_id=383383)
    assert cache["episodes"]["1"] == {"wer": 0.12, "job_id": "abc"}


@pytest.mark.unit
def test_recording_a_second_episode_preserves_the_first(tmp_path):
    record_episode_result(tmp_path, tvdb_id=383383, episode=1, result={"wer": 0.12})
    record_episode_result(tmp_path, tvdb_id=383383, episode=2, result={"wer": 0.15})
    cache = load_series_cache(tmp_path, tvdb_id=383383)
    assert set(cache["episodes"]) == {"1", "2"}
    assert cache["episodes"]["1"]["wer"] == 0.12
    assert cache["episodes"]["2"]["wer"] == 0.15


@pytest.mark.unit
def test_recording_the_same_episode_again_overwrites_not_duplicates(tmp_path):
    record_episode_result(tmp_path, tvdb_id=383383, episode=1, result={"wer": 0.12})
    record_episode_result(tmp_path, tvdb_id=383383, episode=1, result={"wer": 0.05})
    cache = load_series_cache(tmp_path, tvdb_id=383383)
    assert cache["episodes"] == {"1": {"wer": 0.05}}


@pytest.mark.unit
def test_different_series_get_separate_cache_files(tmp_path):
    record_episode_result(tmp_path, tvdb_id=383383, episode=1, result={"wer": 0.12})
    record_episode_result(tmp_path, tvdb_id=999999, episode=1, result={"wer": 0.99})
    assert load_series_cache(tmp_path, 383383)["episodes"]["1"]["wer"] == 0.12
    assert load_series_cache(tmp_path, 999999)["episodes"]["1"]["wer"] == 0.99


@pytest.mark.unit
def test_corrupt_cache_file_is_treated_as_a_miss_not_fatal(tmp_path):
    path = tmp_path / "tvdb-383383.json"
    path.write_text("{not valid json")
    cache = load_series_cache(tmp_path, tvdb_id=383383)
    assert cache == {"tvdb_id": 383383, "episodes": {}}


@pytest.mark.unit
def test_cache_survives_a_crash_mid_write_via_atomic_rename(tmp_path):
    record_episode_result(tmp_path, tvdb_id=383383, episode=1, result={"wer": 0.12})
    # No leftover .tmp file after a successful write.
    assert not (tmp_path / "tvdb-383383.json.tmp").exists()
    # The real file is valid JSON on disk, not just readable through the loader.
    on_disk = json.loads((tmp_path / "tvdb-383383.json").read_text())
    assert on_disk["episodes"]["1"]["wer"] == 0.12
