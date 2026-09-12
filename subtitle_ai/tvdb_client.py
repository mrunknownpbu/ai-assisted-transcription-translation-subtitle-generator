"""TheTVDB API v4 client -- metadata enrichment only, never a runtime
dependency for translation itself. Ported from the prior implementation's
validated design (real production module), adapted to this module's
existing httpx dependency instead of adding `requests`, and pointed at
`/cache/tvdb` instead of the model directory -- v2's /models mount is
read-only (confirmed safe by real GPU inference runs this session), so a
writable cache belongs under /cache alongside the job database and work
directory, not under /models.

Credentials come from environment variables (TVDB_API_KEY, optionally
TVDB_API_PIN for a "user subscription" model key), never hardcoded and
never committed. Without a key configured -- the default in this
deployment -- every public function here returns None/empty, and the
caller (glossary_profile.load_profile) falls back to whatever local
profile/glossary already exists. TheTVDB is metadata enrichment for the
series/episode/cast layer, resolved at most once per job and cached to
disk; it is never queried per translation call, let alone per cue, and an
outage or a missing key can never fail a subtitle job.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

API_BASE = os.environ.get("TVDB_API_BASE", "https://api4.thetvdb.com/v4")
API_KEY = os.environ.get("TVDB_API_KEY")
API_PIN = os.environ.get("TVDB_API_PIN")  # only needed for "user subscription" model keys
REQUEST_TIMEOUT = 10  # seconds -- must never let a slow/hung API stall a job
TOKEN_TTL = 23 * 3600  # TVDB tokens last ~1 month; re-checking daily catches a
                       # revoked/rotated key quickly without hammering /login
CACHE_DIR = Path(os.environ.get("TVDB_CACHE_DIR", "/cache/tvdb"))
CACHE_TTL = 7 * 24 * 3600  # series/episode metadata changes rarely; a week-old
                           # cache entry is still far better than a live call
                           # on every job, and TVDB is never the source of
                           # truth for anything time-critical here


class TvdbUnavailable(RuntimeError):
    """Internal only -- every public function catches this at its own
    boundary and degrades to None/empty rather than let it propagate."""


@dataclass
class _TokenCache:
    token: str | None = None
    obtained_at: float = 0.0

    def valid(self) -> bool:
        return self.token is not None and (time.time() - self.obtained_at) < TOKEN_TTL


_token_cache = _TokenCache()


def configured() -> bool:
    return bool(API_KEY)


def _login() -> str:
    if not API_KEY:
        raise TvdbUnavailable("no TVDB_API_KEY configured")
    payload = {"apikey": API_KEY}
    if API_PIN:
        payload["pin"] = API_PIN
    try:
        resp = httpx.post(f"{API_BASE}/login", json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        token = (resp.json() or {}).get("data", {}).get("token")
    except (httpx.HTTPError, ValueError) as exc:
        raise TvdbUnavailable(f"login failed: {exc}") from exc
    if not token:
        raise TvdbUnavailable("login response carried no token")
    return token


def _token() -> str:
    if not _token_cache.valid():
        _token_cache.token = _login()
        _token_cache.obtained_at = time.time()
    return _token_cache.token


def _get(path: str, *, retry_on_auth_failure: bool = True):
    """GET one TVDB endpoint. Returns the `data` payload, or None on ANY
    failure -- missing key, network error, timeout, rate limit, bad
    response. Callers must treat None as "no enrichment available this
    time", never as an error to surface."""
    try:
        token = _token()
    except TvdbUnavailable:
        return None
    try:
        resp = httpx.get(f"{API_BASE}{path}", headers={"Authorization": f"Bearer {token}"},
                         timeout=REQUEST_TIMEOUT)
        if resp.status_code == 401 and retry_on_auth_failure:
            _token_cache.token = None  # token revoked/rotated server-side -- one retry with a fresh login
            return _get(path, retry_on_auth_failure=False)
        if resp.status_code == 429:
            # Rate-limited: a subtitle job must never block sleeping for a
            # retry here. Skip enrichment for this run.
            return None
        resp.raise_for_status()
        return (resp.json() or {}).get("data")
    except (httpx.HTTPError, ValueError):
        return None


def _cache_path(kind: str, key: str) -> Path:
    return CACHE_DIR / kind / f"{key}.json"


def _cached(kind: str, key: str, fetch):
    path = _cache_path(kind, key)
    try:
        if path.is_file() and (time.time() - path.stat().st_mtime) < CACHE_TTL:
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass  # a corrupt cache entry is refetched, not fatal
    value = fetch()
    if value is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8")
        except OSError:
            pass  # caching is an optimization; failing to write it is not an error
    return value


def series(tvdb_id: int) -> dict | None:
    """Series record: name, original language/country, status, cast, ..."""
    return _cached("series", str(tvdb_id), lambda: _get(f"/series/{tvdb_id}/extended"))


def series_episodes(tvdb_id: int, season_type: str = "official") -> list[dict]:
    """Episodes in the given aired-order season type. 'official' is the
    aired order Sonarr/Jellyfin default to -- never substitute 'dvd' or
    'absolute' order without being explicitly asked."""
    data = _cached(f"episodes-{season_type}", str(tvdb_id),
                   lambda: _get(f"/series/{tvdb_id}/episodes/{season_type}"))
    if not isinstance(data, dict):
        return []
    episodes = data.get("episodes")
    return episodes if isinstance(episodes, list) else []


def episode(tvdb_id: int, season: int, episode_number: int,
           season_type: str = "official") -> dict | None:
    """The aired-order episode record for (season, episode_number), or None
    if TVDB has no confident match -- callers must keep the local SxxExx in
    that case, never invent metadata."""
    for ep in series_episodes(tvdb_id, season_type):
        if ep.get("seasonNumber") == season and ep.get("number") == episode_number:
            return ep
    return None


def characters(tvdb_id: int) -> list[dict]:
    """Cast/character records where TheTVDB has them. Used only to seed or
    enrich the local series entity glossary -- never the sole source, since
    TVDB does not reliably carry local nicknames like "Melo" -> "Melek
    Yucel"; those stay in the local glossary YAML."""
    data = series(tvdb_id)
    if not data:
        return []
    cast = data.get("characters")
    return cast if isinstance(cast, list) else []
