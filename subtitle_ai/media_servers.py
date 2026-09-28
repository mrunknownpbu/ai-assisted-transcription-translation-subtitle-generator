"""Tell Plex and Jellyfin about subtitle files this app just wrote, so they
show up without waiting for the next scheduled library scan.

Targeted, never a full library scan: Plex gets a partial scan of the one
folder (/library/sections/{key}/refresh?path=...), in whichever library
section's location contains it; Jellyfin gets /Library/Media/Updated for
the exact files. Both servers see the library at the same /data/media/...
paths this app does (verified against both 2026-09-28), so paths pass
through unchanged.

Config: PLEX_URL + PLEX_TOKEN, JELLYFIN_URL + JELLYFIN_API_KEY (either pair
may be unset); SUBTITLE_AI_MEDIA_REFRESH=off disables both. Best-effort by
design: a server that is down or refuses only produces a log line -- a
subtitle job's result never depends on it.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import PurePosixPath

import httpx

_logger = logging.getLogger(__name__)
REQUEST_TIMEOUT = 10
_SECTIONS_TTL = 3600.0
_plex_sections: dict = {"at": 0.0, "items": []}


def enabled() -> bool:
    return os.environ.get("SUBTITLE_AI_MEDIA_REFRESH", "").strip().lower() not in {"off", "0", "false", "no"}


def configured() -> bool:
    return enabled() and bool((_env("PLEX_URL") and _env("PLEX_TOKEN"))
                              or (_env("JELLYFIN_URL") and _env("JELLYFIN_API_KEY")))


def _env(name: str) -> str:
    return os.environ.get(name, "").strip().rstrip("/")


def _plex_section_for(folder: str, url: str, token: str) -> str | None:
    now = time.time()
    if now - _plex_sections["at"] > _SECTIONS_TTL:
        resp = httpx.get(f"{url}/library/sections", headers={"X-Plex-Token": token, "Accept": "application/json"},
                         timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        _plex_sections["items"] = [
            (d["key"], loc["path"].rstrip("/") + "/")
            for d in resp.json()["MediaContainer"].get("Directory", []) for loc in d.get("Location", [])]
        _plex_sections["at"] = now
    target = folder.rstrip("/") + "/"
    matches = [(key, loc) for key, loc in _plex_sections["items"] if target.startswith(loc)]
    return max(matches, key=lambda m: len(m[1]))[0] if matches else None


def refresh_plex(paths: list[str]) -> str:
    url, token = _env("PLEX_URL"), _env("PLEX_TOKEN")
    if not url or not token:
        return "not configured"
    folders = sorted({str(PurePosixPath(p).parent) for p in paths})
    done = []
    for folder in folders:
        section = _plex_section_for(folder, url, token)
        if section is None:
            return f"no library contains {folder}"
        resp = httpx.get(f"{url}/library/sections/{section}/refresh", params={"path": folder},
                         headers={"X-Plex-Token": token}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        done.append(section)
    return f"scanned folder in section {', '.join(done)}"


def refresh_jellyfin(paths: list[str]) -> str:
    url, key = _env("JELLYFIN_URL"), _env("JELLYFIN_API_KEY")
    if not url or not key:
        return "not configured"
    resp = httpx.post(f"{url}/Library/Media/Updated",
                      json={"Updates": [{"Path": p, "UpdateType": "Modified"} for p in paths]},
                      headers={"Authorization": f'MediaBrowser Token="{key}"'}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return f"notified {len(paths)} file(s)"


def notify(paths: list[str]) -> dict[str, str]:
    """{server: outcome} -- never raises."""
    if not paths or not enabled():
        return {}
    results = {}
    for name, fn in (("plex", refresh_plex), ("jellyfin", refresh_jellyfin)):
        try:
            results[name] = fn(paths)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            results[name] = f"failed: {exc}"
            _logger.warning("%s refresh failed for %s: %s", name, paths, exc)
    return results
