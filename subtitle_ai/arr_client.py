"""Sonarr / Radarr as the source of truth for what a media file IS.

Given a file path, returns its series or movie with every external id the
*arr apps know (TVDB, TMDB, IMDb, TVmaze) and its original language. They
see the library at the same /data/media/... paths this app does, so a file
maps to its series/movie by the longest matching folder -- no path
translation.

Why not the folder's `{tvdb-<id>}` tag alone (glossary_profile's original
rule)? Checked against the live library 2026-09-28: 3 of 1,984 Sonarr
series have a broken or missing tag ("{tvbd-443608}", "{tvdb-449887 }"),
6 have a tag that disagrees with Sonarr's own tvdbId (re-matched series),
and 7 of 3,432 Radarr movies have no `{tmdb-<id>}` tag. The tag stays as
the fallback when the *arr apps are unset or unreachable.

Config: SONARR_URL / SONARR_API_KEY, RADARR_URL / RADARR_API_KEY (either
may be unset). The full series/movie lists are fetched at most once per
INDEX_TTL and persisted under /cache/arr, so a lookup is a dict scan, and
an outage falls back to the last good index instead of to nothing.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import httpx

_logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.environ.get("SUBTITLE_AI_ARR_CACHE", "/cache/arr"))
INDEX_TTL = 3600.0
FAILED_RETRY = 300.0      # after a failed fetch, don't retry for 5 minutes
REQUEST_TIMEOUT = 20

# Sonarr/Radarr language names -> the ISO 639-1 codes this app uses.
LANGUAGE_CODES = {
    "english": "en", "turkish": "tr", "korean": "ko", "japanese": "ja", "chinese": "zh",
    "thai": "th", "french": "fr", "spanish": "es", "german": "de", "italian": "it",
    "portuguese": "pt", "portuguese (brazil)": "pt", "russian": "ru", "arabic": "ar",
    "hindi": "hi", "tamil": "ta", "telugu": "te", "malayalam": "ml", "indonesian": "id",
    "malay": "ms", "filipino": "tl", "tagalog": "tl", "vietnamese": "vi", "dutch": "nl",
    "polish": "pl", "swedish": "sv", "danish": "da", "norwegian": "no", "finnish": "fi",
    "greek": "el", "hebrew": "he", "persian": "fa", "urdu": "ur", "bengali": "bn",
    "ukrainian": "uk", "czech": "cs", "hungarian": "hu", "romanian": "ro",
}


@dataclass
class MediaInfo:
    kind: str                       # "series" | "movie"
    title: str
    year: int | None
    path: str                       # the series/movie folder, as the *arr app sees it
    ids: dict = field(default_factory=dict)   # tvdb / tmdb / imdb / tvmaze
    original_language: str | None = None     # ISO 639-1, None if unknown/unmapped
    arr_id: int | None = None

    @property
    def tvdb_id(self) -> int | None:
        return self.ids.get("tvdb")


def _app(kind: str) -> tuple[str, str] | None:
    prefix = "SONARR" if kind == "series" else "RADARR"
    url = os.environ.get(f"{prefix}_URL", "").strip().rstrip("/")
    key = os.environ.get(f"{prefix}_API_KEY", "").strip()
    return (url, key) if url and key else None


def configured() -> bool:
    return _app("series") is not None or _app("movie") is not None


def _language(record: dict) -> str | None:
    name = ((record.get("originalLanguage") or {}).get("name") or "").strip().lower()
    return LANGUAGE_CODES.get(name)


def _to_info(kind: str, r: dict) -> MediaInfo:
    ids = {"tvdb": r.get("tvdbId") or None, "tmdb": r.get("tmdbId") or None,
           "imdb": r.get("imdbId") or None, "tvmaze": r.get("tvMazeId") or None}
    return MediaInfo(kind=kind, title=r.get("title") or "", year=r.get("year") or None,
                     path=r.get("path") or "", ids={k: v for k, v in ids.items() if v},
                     original_language=_language(r), arr_id=r.get("id"))


class _Index:
    def __init__(self, kind: str):
        self.kind = kind
        self.items: list[MediaInfo] = []
        self.fetched_at = 0.0
        self.failed_at = 0.0
        self.lock = threading.Lock()

    def _cache_file(self) -> Path:
        return CACHE_DIR / f"{self.kind}.json"

    def _fetch(self) -> list[MediaInfo] | None:
        app = _app(self.kind)
        if app is None:
            return None
        url, key = app
        endpoint = "series" if self.kind == "series" else "movie"
        try:
            resp = httpx.get(f"{url}/api/v3/{endpoint}", headers={"X-Api-Key": key}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            records = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            _logger.warning("%s unavailable: %s", "Sonarr" if self.kind == "series" else "Radarr", exc)
            return None
        items = [_to_info(self.kind, r) for r in records if r.get("path")]
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self._cache_file().write_text(json.dumps([i.__dict__ for i in items], ensure_ascii=False),
                                          encoding="utf-8")
        except OSError:
            pass
        return items

    def refresh(self) -> None:
        """Fetch now (blocking). Used in the background and at first use."""
        with self.lock:
            now = time.time()
            if now - self.failed_at < FAILED_RETRY or not _app(self.kind):
                return
            items = self._fetch()
            if items is not None:
                self.items, self.fetched_at = items, now
            else:
                self.failed_at = now
            self.refreshing = False

    def get(self) -> list[MediaInfo]:
        """Never blocks on the network once any index exists: the Radarr
        list takes ~8s here, which must not stall a job submission. A stale
        index is served while a background thread refreshes it; only the
        very first use with no index at all (not even on disk) waits."""
        if not self.items:
            with self.lock:
                if not self.items:
                    try:  # last good index from a previous run
                        raw = json.loads(self._cache_file().read_text(encoding="utf-8"))
                        self.items = [MediaInfo(**r) for r in raw]
                        self.fetched_at = self._cache_file().stat().st_mtime
                    except (OSError, ValueError, TypeError):
                        pass
            if not self.items:
                self.refresh()
                return self.items
        if time.time() - self.fetched_at >= INDEX_TTL and not getattr(self, "refreshing", False):
            self.refreshing = True
            threading.Thread(target=self.refresh, name=f"arr-{self.kind}-refresh", daemon=True).start()
        return self.items


_indexes = {"series": _Index("series"), "movie": _Index("movie")}


def _absolute(path: str) -> str:
    """Job paths are stored relative to the media root; *arr paths are absolute."""
    if path.startswith("/"):
        return path
    root = os.environ.get("SUBTITLE_AI_MEDIA_ROOT", "/data")
    return str(PurePosixPath(root) / path)


def lookup(path: str, kinds: tuple[str, ...] = ("series", "movie")) -> MediaInfo | None:
    """The series or movie whose folder contains `path` (a file or folder,
    absolute or media-root-relative), or None."""
    if not path or not configured():
        return None
    target = _absolute(path).rstrip("/") + "/"
    best: MediaInfo | None = None
    for kind in kinds:
        for item in _indexes[kind].get():
            folder = item.path.rstrip("/") + "/"
            if target.startswith(folder) and (best is None or len(folder) > len(best.path.rstrip("/") + "/")):
                best = item
    return best


def all_items(kind: str) -> list[MediaInfo]:
    return list(_indexes[kind].get()) if _app(kind) else []
