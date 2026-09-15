"""Integration tests for the media-library browse/register endpoints, against a real
synthetic media file placed in a fixture 'library' directory rather than uploaded."""
import pytest
from fastapi.testclient import TestClient

from tests.fixtures.synthetic_media import build_multi_stream_fixture


@pytest.fixture
def library_root(tmp_path):
    root = tmp_path / "library"
    (root / "Show" / "Season 01").mkdir(parents=True)
    build_multi_stream_fixture(root / "Show" / "Season 01", speech_text="Library registration test.")
    # A non-media file in the same directory must never show up in browse results.
    (root / "Show" / "Season 01" / "notes.txt").write_text("not a media file")
    return root


@pytest.fixture
def client(monkeypatch, library_root):
    monkeypatch.setenv("SUBTITLE_WORKER_THREADS", "0")
    monkeypatch.setenv("SUBTITLE_LIBRARY_DIR", str(library_root))
    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.mark.integration
def test_browse_root_lists_only_the_show_directory(client):
    resp = client.get("/api/library/browse")
    assert resp.status_code == 200
    entries = resp.json()
    assert entries == [{"name": "Show", "path": "Show", "is_dir": True}]


@pytest.mark.integration
def test_browse_descends_into_subdirectory_and_filters_non_media_files(client):
    resp = client.get("/api/library/browse", params={"path": "Show/Season 01"})
    assert resp.status_code == 200
    entries = resp.json()
    names = {e["name"] for e in entries}
    assert "notes.txt" not in names
    assert any(name.endswith(".mkv") for name in names)
    assert all(not e["is_dir"] for e in entries)  # only the media file, no subdirs here


@pytest.mark.integration
def test_browse_rejects_traversal(client):
    resp = client.get("/api/library/browse", params={"path": "../../etc"})
    assert resp.status_code == 422


@pytest.mark.integration
def test_register_media_from_library_without_copying(client, library_root):
    listing = client.get("/api/library/browse", params={"path": "Show/Season 01"}).json()
    media_entry = next(e for e in listing if not e["is_dir"])

    resp = client.post("/api/media/register", json={"path": media_entry["path"]})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["audio_streams"]) == 3  # the multi-stream fixture: tone, speech, silence
    assert body["filename"] == media_entry["name"]


@pytest.mark.integration
def test_register_extracts_tvdb_id_from_sonarr_style_folder_name(client, library_root):
    (library_root / "If You Love (2023) {tvdb-435293}" / "Season 01").mkdir(parents=True)
    build_multi_stream_fixture(
        library_root / "If You Love (2023) {tvdb-435293}" / "Season 01", speech_text="Tvdb extraction test.",
    )
    listing = client.get("/api/library/browse", params={"path": "If You Love (2023) {tvdb-435293}/Season 01"}).json()
    media_entry = next(e for e in listing if not e["is_dir"])

    resp = client.post("/api/media/register", json={"path": media_entry["path"]})

    assert resp.status_code == 200
    assert resp.json()["tvdb_id"] == 435293


@pytest.mark.integration
def test_register_leaves_tvdb_id_null_without_the_folder_convention(client):
    listing = client.get("/api/library/browse", params={"path": "Show/Season 01"}).json()
    media_entry = next(e for e in listing if not e["is_dir"])

    resp = client.post("/api/media/register", json={"path": media_entry["path"]})

    assert resp.json()["tvdb_id"] is None


@pytest.mark.integration
def test_register_nonexistent_file_404s(client):
    resp = client.post("/api/media/register", json={"path": "Show/Season 01/missing.mkv"})
    assert resp.status_code == 404


@pytest.mark.integration
def test_register_rejects_traversal(client):
    resp = client.post("/api/media/register", json={"path": "../../etc/passwd"})
    assert resp.status_code in (404, 422)  # sandboxed-but-missing (404) or explicitly rejected (422) — never 200


@pytest.mark.integration
def test_library_endpoints_404_when_not_configured(monkeypatch):
    monkeypatch.setenv("SUBTITLE_WORKER_THREADS", "0")
    monkeypatch.delenv("SUBTITLE_LIBRARY_DIR", raising=False)
    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        assert c.get("/api/library/browse").status_code == 404
        assert c.post("/api/media/register", json={"path": "x.mkv"}).status_code == 404
