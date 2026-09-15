import pytest
from fastapi import HTTPException

from app.api.routes.references import resolve_reference_path
from app.config import Settings


def _settings(reference_dir=None):
    return Settings(reference_dir=reference_dir)


@pytest.mark.unit
def test_resolves_a_valid_relative_path(tmp_path):
    (tmp_path / "Show").mkdir()
    (tmp_path / "Show" / "episode.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n")

    resolved = resolve_reference_path(_settings(tmp_path), "Show/episode.srt")

    assert resolved == (tmp_path / "Show" / "episode.srt").resolve()


@pytest.mark.unit
def test_rejects_parent_directory_traversal(tmp_path):
    (tmp_path / "references").mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("do not read this")

    with pytest.raises(HTTPException) as excinfo:
        resolve_reference_path(_settings(tmp_path / "references"), "../secret.txt")

    assert excinfo.value.status_code == 422


@pytest.mark.unit
def test_absolute_looking_input_is_sandboxed_under_the_root_not_escaped(tmp_path):
    reference_root = tmp_path / "references"
    reference_root.mkdir()

    resolved = resolve_reference_path(_settings(reference_root), "/etc/passwd")

    assert resolved == (reference_root / "etc" / "passwd").resolve()
    assert reference_root.resolve() in resolved.parents


@pytest.mark.unit
def test_rejects_deeply_nested_traversal(tmp_path):
    reference_root = tmp_path / "references"
    reference_root.mkdir()

    with pytest.raises(HTTPException) as excinfo:
        resolve_reference_path(_settings(reference_root), "a/b/../../../etc/passwd")

    assert excinfo.value.status_code == 422


@pytest.mark.unit
def test_reference_dir_not_configured_returns_404(tmp_path):
    with pytest.raises(HTTPException) as excinfo:
        resolve_reference_path(_settings(None), "anything.srt")

    assert excinfo.value.status_code == 404


@pytest.mark.unit
def test_root_itself_is_a_valid_path(tmp_path):
    reference_root = tmp_path / "references"
    reference_root.mkdir()

    resolved = resolve_reference_path(_settings(reference_root), "")

    assert resolved == reference_root.resolve()
