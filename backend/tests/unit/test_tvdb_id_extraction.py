import pytest

from app.pipeline.stage8_translation.tvdb_client import extract_tvdb_id_from_path


@pytest.mark.unit
def test_extracts_id_from_sonarr_style_folder_convention():
    path = "/library/media/drama/turkish/If You Love (2023) {tvdb-435293}/Season 01/S01E01.mkv"
    assert extract_tvdb_id_from_path(path) == 435293


@pytest.mark.unit
def test_returns_none_when_no_tvdb_segment_present():
    assert extract_tvdb_id_from_path("/library/media/Some Show/Season 01/S01E01.mkv") is None


@pytest.mark.unit
def test_returns_none_for_empty_path():
    assert extract_tvdb_id_from_path("") is None


@pytest.mark.unit
def test_ignores_other_brace_content_and_only_matches_tvdb_prefix():
    assert extract_tvdb_id_from_path("/library/Show {imdb-tt1234567}/S01E01.mkv") is None


@pytest.mark.unit
def test_matches_first_occurrence_when_a_path_has_multiple_brace_tags():
    path = "/library/Show {tvdb-100}/Season {tvdb-999}/S01E01.mkv"
    assert extract_tvdb_id_from_path(path) == 100
