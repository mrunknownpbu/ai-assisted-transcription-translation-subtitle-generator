import pytest
from fastapi import HTTPException

from app.api.routes.media import resolve_library_path
from app.config import Settings


def _settings(library_dir=None):
    return Settings(library_dir=library_dir)


@pytest.mark.unit
def test_resolves_a_valid_relative_path(tmp_path):
    (tmp_path / "Show").mkdir()
    (tmp_path / "Show" / "episode.mkv").write_bytes(b"fake")

    resolved = resolve_library_path(_settings(tmp_path), "Show/episode.mkv")

    assert resolved == (tmp_path / "Show" / "episode.mkv").resolve()


@pytest.mark.unit
def test_rejects_parent_directory_traversal(tmp_path):
    (tmp_path / "library").mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("do not read this")

    with pytest.raises(HTTPException) as excinfo:
        resolve_library_path(_settings(tmp_path / "library"), "../secret.txt")

    assert excinfo.value.status_code == 422


@pytest.mark.unit
def test_absolute_looking_input_is_sandboxed_under_the_root_not_escaped(tmp_path):
    """A leading '/' in user input never re-roots the lookup at the container's real
    filesystem root — it's stripped and treated as relative to the library root, so
    '/etc/passwd' safely resolves to '<library_root>/etc/passwd' (almost certainly
    nonexistent) rather than the real /etc/passwd."""
    library_root = tmp_path / "library"
    library_root.mkdir()

    resolved = resolve_library_path(_settings(library_root), "/etc/passwd")

    assert resolved == (library_root / "etc" / "passwd").resolve()
    assert library_root.resolve() in resolved.parents


@pytest.mark.unit
def test_rejects_deeply_nested_traversal(tmp_path):
    library_root = tmp_path / "library"
    library_root.mkdir()

    with pytest.raises(HTTPException) as excinfo:
        resolve_library_path(_settings(library_root), "a/b/../../../etc/passwd")

    assert excinfo.value.status_code == 422


@pytest.mark.unit
def test_library_not_configured_returns_404(tmp_path):
    with pytest.raises(HTTPException) as excinfo:
        resolve_library_path(_settings(None), "anything.mkv")

    assert excinfo.value.status_code == 404


@pytest.mark.unit
def test_root_itself_is_a_valid_path(tmp_path):
    library_root = tmp_path / "library"
    library_root.mkdir()

    resolved = resolve_library_path(_settings(library_root), "")

    assert resolved == library_root.resolve()
