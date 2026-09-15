import pytest
from fastapi.testclient import TestClient

from tests.fixtures.synthetic_media import build_multi_stream_fixture


@pytest.fixture
def library_root(tmp_path):
    root = tmp_path / "library"
    (root / "Show" / "Season 01").mkdir(parents=True)
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
def test_reference_options_lists_embedded_subtitle_streams_from_stored_ffprobe_data(client, library_root):
    build_multi_stream_fixture(library_root / "Show" / "Season 01", speech_text="Hello there.")
    listing = client.get("/api/library/browse", params={"path": "Show/Season 01"}).json()
    media_entry = next(e for e in listing if not e["is_dir"])
    media = client.post("/api/media/register", json={"path": media_entry["path"]}).json()

    resp = client.get(f"/api/media/{media['id']}/reference-options")
    assert resp.status_code == 200
    body = resp.json()
    # the synthetic fixture has no subtitle streams -- this just proves the shape/route work
    assert body["embedded_subtitle_streams"] == []
    assert body["sibling_subtitle_files"] == []


@pytest.mark.integration
def test_reference_options_discovers_sibling_subtitle_files(client, library_root):
    build_multi_stream_fixture(library_root / "Show" / "Season 01", speech_text="Hello there.")
    listing = client.get("/api/library/browse", params={"path": "Show/Season 01"}).json()
    media_entry = next(e for e in listing if not e["is_dir"])
    media = client.post("/api/media/register", json={"path": media_entry["path"]}).json()

    sibling_dir = library_root / "Show" / "Season 01"
    media_stem = media["filename"].rsplit(".", 1)[0]
    sidecar_name = f"{media_stem}.en.hi.srt"
    (sibling_dir / sidecar_name).write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n")
    (sibling_dir / "notes.txt").write_text("not a subtitle")

    resp = client.get(f"/api/media/{media['id']}/reference-options")
    body = resp.json()
    names = {s["name"] for s in body["sibling_subtitle_files"]}
    assert names == {sidecar_name}
    entry = body["sibling_subtitle_files"][0]
    assert entry["path"] == f"Show/Season 01/{sidecar_name}"


@pytest.mark.integration
def test_reference_options_only_lists_this_episodes_own_sidecar_not_the_whole_season(client, library_root):
    build_multi_stream_fixture(library_root / "Show" / "Season 01", speech_text="Hello there.")
    listing = client.get("/api/library/browse", params={"path": "Show/Season 01"}).json()
    media_entry = next(e for e in listing if not e["is_dir"])
    media = client.post("/api/media/register", json={"path": media_entry["path"]}).json()

    # A season folder holds one sidecar per episode -- a different episode's file must not
    # be offered as if it could be this job's reference.
    season_dir = library_root / "Show" / "Season 01"
    media_stem = media["filename"].rsplit(".", 1)[0]
    (season_dir / f"{media_stem}.en.hi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nMine\n")
    (season_dir / "Show S01E99.en.hi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nNot mine\n")

    resp = client.get(f"/api/media/{media['id']}/reference-options")
    names = {s["name"] for s in resp.json()["sibling_subtitle_files"]}
    assert names == {f"{media_stem}.en.hi.srt"}


@pytest.mark.integration
def test_reference_options_404s_for_missing_media(client):
    assert client.get("/api/media/does-not-exist/reference-options").status_code == 404
