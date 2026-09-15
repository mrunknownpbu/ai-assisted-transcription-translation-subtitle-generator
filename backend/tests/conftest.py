import os
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True, scope="function")
def isolated_data_dirs(monkeypatch):
    """Every test gets its own scratch data dir tree and a fresh Settings/DB, so tests
    never share state or touch the real /data volume."""
    tmp = tempfile.mkdtemp(prefix="subtitle_platform_test_")
    monkeypatch.setenv("SUBTITLE_MEDIA_DIR", str(Path(tmp) / "media"))
    monkeypatch.setenv("SUBTITLE_OUTPUT_DIR", str(Path(tmp) / "output"))
    monkeypatch.setenv("SUBTITLE_DB_PATH", str(Path(tmp) / "db" / "test.db"))
    monkeypatch.setenv("SUBTITLE_MODELS_DIR", str(Path(tmp) / "models"))
    monkeypatch.setenv("SUBTITLE_WORK_DIR", str(Path(tmp) / "work"))

    from app.config import get_settings
    get_settings.cache_clear()

    import app.db.session as session_module
    session_module._engine = None
    session_module._SessionLocal = None

    yield

    get_settings.cache_clear()
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def ffmpeg_fixture_dir(tmp_path_factory):
    """Session-scoped-ish dir for generated synthetic media, built lazily by
    tests/fixtures/synthetic_media.py so we never ship real dialogue audio in the repo."""
    return tmp_path_factory.mktemp("synthetic_media")
