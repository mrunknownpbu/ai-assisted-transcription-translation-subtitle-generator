"""Optional TVDB integration: auto-populate a translation glossary from a series' cast list
so entity protection (glossary.py) works with zero local configuration — just a TVDB series
ID. Entirely best-effort: every public function catches all failure modes internally and
returns None/empty rather than ever failing a job, since this is metadata enrichment, not a
required dependency.

Ported from the sibling project's version specifically because it filters the cast list to
actual actors — the other sibling project's otherwise-identical client returns the raw list
unfiltered, and a real TVDB series roster also carries writer/director credits under the
same endpoint, which are not character names and must not be protected as if they were.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

import httpx

logger = logging.getLogger("subtitle_platform.pipeline.translation.tvdb")

_TVDB_FOLDER_PATTERN = re.compile(r"\{tvdb-(\d+)\}")


def extract_tvdb_id_from_path(path: str) -> int | None:
    """Pulls a series TVDB id out of a "{tvdb-XXXXX}" path segment -- the convention
    Sonarr and other arr-stack media managers already stamp onto library folder names, so a
    library file registered from a real media server usually carries this for free. Returns
    None (never raises) when the path has no such segment; this is enrichment, not a
    required input."""
    match = _TVDB_FOLDER_PATTERN.search(path)
    return int(match.group(1)) if match else None

API_BASE = os.environ.get("TVDB_API_BASE", "https://api4.thetvdb.com/v4")
CACHE_DIR = Path(os.environ.get("TVDB_CACHE_DIR", "/config/subtitleai/models/tvdb_cache"))
CACHE_TTL = 7 * 24 * 3600
TOKEN_TTL = 23 * 3600
REQUEST_TIMEOUT = 10.0

_token: str | None = None
_token_fetched_at: float = 0.0


class TvdbUnavailable(Exception):
    pass


def configured() -> bool:
    return bool(os.environ.get("TVDB_API_KEY"))


def _cache_path(kind: str, key: str) -> Path:
    return CACHE_DIR / kind / f"{key}.json"


def _read_cache(kind: str, key: str) -> dict | list | None:
    path = _cache_path(kind, key)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - payload["cached_at"] > CACHE_TTL:
            return None
        return payload["data"]
    except Exception:
        return None  # corrupt cache entry — silently refetch rather than fail the job


def _write_cache(kind: str, key: str, data) -> None:
    try:
        path = _cache_path(kind, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"cached_at": time.time(), "data": data}), encoding="utf-8")
    except Exception:
        pass  # cache is a pure optimization; failing to write it must never fail the job


def _login() -> str | None:
    global _token, _token_fetched_at
    api_key = os.environ.get("TVDB_API_KEY")
    if not api_key:
        return None
    try:
        resp = httpx.post(
            f"{API_BASE}/login",
            json={"apikey": api_key, **({"pin": os.environ["TVDB_API_PIN"]} if os.environ.get("TVDB_API_PIN") else {})},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        _token = resp.json()["data"]["token"]
        _token_fetched_at = time.time()
        return _token
    except Exception as exc:
        logger.info("TVDB login failed (metadata enrichment will be skipped): %s", exc)
        return None


def _get(path: str, *, retry_on_auth_failure: bool = True) -> dict | None:
    global _token
    if _token is None or (time.time() - _token_fetched_at) > TOKEN_TTL:
        _token = _login()
    if _token is None:
        return None
    try:
        resp = httpx.get(f"{API_BASE}{path}", headers={"Authorization": f"Bearer {_token}"}, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 401 and retry_on_auth_failure:
            _token = None
            return _get(path, retry_on_auth_failure=False)
        if resp.status_code == 429:
            logger.info("TVDB rate-limited; skipping enrichment for this job rather than blocking on a retry")
            return None
        resp.raise_for_status()
        return resp.json().get("data")
    except Exception as exc:
        logger.info("TVDB request failed (metadata enrichment will be skipped): %s", exc)
        return None


def series_extended(tvdb_id: int) -> dict | None:
    cached = _read_cache("series", str(tvdb_id))
    if cached is not None:
        return cached
    data = _get(f"/series/{tvdb_id}/extended")
    if data is not None:
        _write_cache("series", str(tvdb_id), data)
    return data


def characters(tvdb_id: int) -> list[dict]:
    data = series_extended(tvdb_id)
    if not data:
        return []
    cast = data.get("characters")
    if not isinstance(cast, list):
        return []
    # A series roster also carries writer/director credits under the same endpoint — only
    # actual cast entries are character names worth protecting during translation.
    return [c for c in cast if c.get("peopleType") == "Actor" and c.get("name")]


def glossary_from_characters(tvdb_id: int):
    """Builds a working entity glossary directly from TVDB cast, with zero local glossary
    files. Returns `list[glossary.Entity]` (imported locally to avoid a hard dependency
    cycle for callers that only need the raw `characters()` list)."""
    from app.pipeline.stage8_translation.glossary import Entity

    entities = []
    for c in characters(tvdb_id):
        name = c["name"].strip()
        parts = name.split()
        first = parts[0] if parts else name
        surface_forms = [name] if first == name else [name, first]
        entities.append(Entity(canonical=name, surface_forms=surface_forms))
    return entities
