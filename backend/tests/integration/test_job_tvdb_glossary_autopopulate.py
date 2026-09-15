"""Job creation should auto-populate a translation glossary from TVDB cast data when the
media file carries a tvdb_id (auto-extracted at registration from a "{tvdb-XXXXX}" library
folder -- see test_library_endpoints.py) and the caller supplied neither an explicit
glossary nor an explicit tvdb_id override. Network calls are mocked: this tests the wiring
in app/api/routes/jobs.py, not the real TVDB API (that's tvdb_client's own concern)."""
import pytest
from fastapi.testclient import TestClient

from app.db.models import Job
from app.db.session import session_scope
from app.pipeline.stage8_translation.glossary import Entity
from tests.fixtures.synthetic_media import build_multi_stream_fixture


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SUBTITLE_WORKER_THREADS", "0")
    monkeypatch.setenv("SUBTITLE_MEDIA_DIR", str(tmp_path / "media"))
    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


def _register_media_with_tvdb_id(client, tmp_path, tvdb_id: int | None):
    media_dir = tmp_path / f"Show {{tvdb-{tvdb_id}}}" if tvdb_id else tmp_path / "Show"
    media_dir.mkdir(parents=True)
    media_path = build_multi_stream_fixture(media_dir, speech_text="Job creation glossary test.")
    with open(media_path, "rb") as f:
        resp = client.post("/api/media/upload", files={"file": (media_path.name, f, "video/x-matroska")})
    media = resp.json()
    # /upload never sees a library path, so simulate what /register would have picked up.
    if tvdb_id is not None:
        with session_scope() as session:
            from app.db.models import MediaFile
            session.get(MediaFile, media["id"]).tvdb_id = tvdb_id
            session.commit()
    stream_id = media["audio_streams"][0]["id"]
    return media["id"], stream_id


@pytest.mark.integration
def test_create_job_auto_populates_glossary_from_tvdb_cast(client, tmp_path, monkeypatch):
    media_id, stream_id = _register_media_with_tvdb_id(client, tmp_path, tvdb_id=435293)

    called_with = []

    def fake_glossary_from_characters(tvdb_id):
        called_with.append(tvdb_id)
        return [Entity(canonical="Ateş", surface_forms=["Ateş"])]

    monkeypatch.setattr("app.api.routes.jobs.glossary_from_characters", fake_glossary_from_characters)

    resp = client.post("/api/jobs", json={
        "media_file_id": media_id, "audio_stream_id": stream_id, "target_languages": ["english"],
    })
    assert resp.status_code == 200
    job_id = resp.json()["id"]

    assert called_with == [435293]
    with session_scope() as session:
        job = session.get(Job, job_id)
        assert job.tvdb_id == 435293
        assert job.glossary_entities == [{"canonical": "Ateş", "surface_forms": ["Ateş"]}]


@pytest.mark.integration
def test_create_job_skips_tvdb_lookup_when_media_has_no_tvdb_id(client, tmp_path, monkeypatch):
    media_id, stream_id = _register_media_with_tvdb_id(client, tmp_path, tvdb_id=None)

    called = []
    monkeypatch.setattr("app.api.routes.jobs.glossary_from_characters", lambda tvdb_id: called.append(tvdb_id) or [])

    resp = client.post("/api/jobs", json={
        "media_file_id": media_id, "audio_stream_id": stream_id, "target_languages": ["english"],
    })
    assert resp.status_code == 200
    assert called == []
    with session_scope() as session:
        job = session.get(Job, resp.json()["id"])
        assert job.tvdb_id is None
        assert job.glossary_entities is None


@pytest.mark.integration
def test_create_job_merges_an_explicit_glossary_with_tvdb_autopopulate(client, tmp_path, monkeypatch):
    """A real show's TVDB cast entry is routinely incomplete (confirmed real case: 'Evren',
    a recurring character in Sen Çal Kapımı / Love Is In The Air tvdb-383383, literally
    mistranslated as 'Universe' because TVDB's own cast list for that series never included
    him at all). An explicit glossary must supplement TVDB's list, not replace it wholesale
    -- otherwise adding the one missing name means retyping every other cast member by hand."""
    media_id, stream_id = _register_media_with_tvdb_id(client, tmp_path, tvdb_id=435293)

    monkeypatch.setattr("app.api.routes.jobs.glossary_from_characters",
                         lambda tvdb_id: [Entity(canonical="Ateş", surface_forms=["Ateş"])])

    resp = client.post("/api/jobs", json={
        "media_file_id": media_id, "audio_stream_id": stream_id, "target_languages": ["english"],
        "glossary_entities": [{"canonical": "Manual Name", "aliases": ["Manual Name"]}],
    })
    assert resp.status_code == 200
    with session_scope() as session:
        job = session.get(Job, resp.json()["id"])
        assert {e["canonical"] for e in job.glossary_entities} == {"Ateş", "Manual Name"}


@pytest.mark.integration
def test_create_job_explicit_entry_overrides_a_colliding_tvdb_canonical_name(client, tmp_path, monkeypatch):
    media_id, stream_id = _register_media_with_tvdb_id(client, tmp_path, tvdb_id=435293)

    monkeypatch.setattr("app.api.routes.jobs.glossary_from_characters",
                         lambda tvdb_id: [Entity(canonical="Ateş", surface_forms=["Ateş"])])

    resp = client.post("/api/jobs", json={
        "media_file_id": media_id, "audio_stream_id": stream_id, "target_languages": ["english"],
        "glossary_entities": [{"canonical": "Ateş", "aliases": ["Ateş", "Ateş Bey"]}],
    })
    assert resp.status_code == 200
    with session_scope() as session:
        job = session.get(Job, resp.json()["id"])
        assert job.glossary_entities == [{"canonical": "Ateş", "aliases": ["Ateş", "Ateş Bey"]}]


@pytest.mark.integration
def test_create_job_respects_an_explicit_tvdb_id_override(client, tmp_path, monkeypatch):
    media_id, stream_id = _register_media_with_tvdb_id(client, tmp_path, tvdb_id=435293)

    called_with = []
    monkeypatch.setattr("app.api.routes.jobs.glossary_from_characters",
                         lambda tvdb_id: called_with.append(tvdb_id) or [])

    resp = client.post("/api/jobs", json={
        "media_file_id": media_id, "audio_stream_id": stream_id, "target_languages": ["english"],
        "tvdb_id": 999999,
    })
    assert resp.status_code == 200
    assert called_with == [999999]  # explicit override wins over the media file's auto-extracted id
    with session_scope() as session:
        job = session.get(Job, resp.json()["id"])
        assert job.tvdb_id == 999999


def _submit_direct_translation(client, tmp_path, tvdb_id=None, glossary_entities=None):
    srt_path = tmp_path / "input.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
    data = {"target_languages": "spanish"}
    if tvdb_id is not None:
        data["tvdb_id"] = str(tvdb_id)
    if glossary_entities is not None:
        import json
        data["glossary_entities"] = json.dumps(glossary_entities)
    with open(srt_path, "rb") as f:
        return client.post("/api/jobs/direct-translation", data=data, files={"file": ("input.srt", f, "text/plain")})


@pytest.mark.integration
def test_direct_translation_auto_populates_glossary_from_tvdb_cast(client, tmp_path, monkeypatch):
    called_with = []

    def fake_glossary_from_characters(tvdb_id):
        called_with.append(tvdb_id)
        return [Entity(canonical="Ateş", surface_forms=["Ateş"])]

    monkeypatch.setattr("app.api.routes.jobs.glossary_from_characters", fake_glossary_from_characters)

    resp = _submit_direct_translation(client, tmp_path, tvdb_id=435293)

    assert resp.status_code == 200
    assert called_with == [435293]
    with session_scope() as session:
        job = session.get(Job, resp.json()["id"])
        assert job.tvdb_id == 435293
        assert job.glossary_entities == [{"canonical": "Ateş", "surface_forms": ["Ateş"]}]


@pytest.mark.integration
def test_direct_translation_skips_tvdb_lookup_without_a_tvdb_id(client, tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr("app.api.routes.jobs.glossary_from_characters", lambda tvdb_id: called.append(tvdb_id) or [])

    resp = _submit_direct_translation(client, tmp_path)

    assert resp.status_code == 200
    assert called == []
    with session_scope() as session:
        job = session.get(Job, resp.json()["id"])
        assert job.glossary_entities is None


@pytest.mark.integration
def test_create_job_merges_all_three_layers_tvdb_profile_and_explicit(client, tmp_path, monkeypatch):
    """End-to-end regression test for the real 'Evren' -> 'Mr. Universe' case: TVDB's own
    cast list is missing a character, a local glossary-profile YAML supplies it, and an
    explicit per-job entry still wins over both when it collides."""
    media_id, stream_id = _register_media_with_tvdb_id(client, tmp_path, tvdb_id=383383)

    monkeypatch.setattr("app.api.routes.jobs.glossary_from_characters",
                         lambda tvdb_id: [Entity(canonical="Eda Yıldız", surface_forms=["Eda Yıldız", "Eda"])])

    profile_dir = tmp_path / "glossary_profiles"
    profile_dir.mkdir()
    (profile_dir / "series.yaml").write_text("""
tvdb_id: 383383
entities:
  - canonical: Evren
    protected: true
    aliases: []
  - canonical: Eda Yıldız
    protected: true
    aliases: [Eda, Edacım]
""")
    monkeypatch.setenv("SUBTITLE_GLOSSARY_PROFILE_DIR", str(profile_dir))
    from app.config import get_settings
    get_settings.cache_clear()  # get_settings is @lru_cache'd -- the client fixture already
    # triggered and cached it before this env var was set, so a fresh app needs a clear too.
    from app.main import create_app
    app = create_app()
    from fastapi.testclient import TestClient
    with TestClient(app) as scoped_client:
        resp = scoped_client.post("/api/jobs", json={
            "media_file_id": media_id, "audio_stream_id": stream_id, "target_languages": ["english"],
            "glossary_entities": [{"canonical": "Serkan Bolat", "aliases": ["Serkan"]}],
        })
    assert resp.status_code == 200
    with session_scope() as session:
        job = session.get(Job, resp.json()["id"])
        by_canonical = {e["canonical"]: e for e in job.glossary_entities}
        assert set(by_canonical) == {"Eda Yıldız", "Evren", "Serkan Bolat"}
        # the profile's richer alias list wins over the bare TVDB one for the same name
        assert by_canonical["Eda Yıldız"]["surface_forms"] == ["Eda Yıldız", "Eda", "Edacım"]


@pytest.mark.integration
def test_direct_translation_merges_an_explicit_glossary_with_tvdb_autopopulate(client, tmp_path, monkeypatch):
    """Same real motivation as the audio-pipeline version: a missing TVDB cast entry (the
    confirmed 'Evren' case) should be addable without losing auto-population for everyone
    else in the cast."""
    monkeypatch.setattr("app.api.routes.jobs.glossary_from_characters",
                         lambda tvdb_id: [Entity(canonical="Ateş", surface_forms=["Ateş"])])

    resp = _submit_direct_translation(
        client, tmp_path, tvdb_id=435293, glossary_entities=[{"canonical": "Manual Name", "aliases": ["Manual Name"]}],
    )

    assert resp.status_code == 200
    with session_scope() as session:
        job = session.get(Job, resp.json()["id"])
        assert {e["canonical"] for e in job.glossary_entities} == {"Ateş", "Manual Name"}
