"""Persistent per-series evaluation cache: records each episode's computed accuracy
metrics (chrF, word-F1, whole-transcript WER) so a later evaluation run for the same
series doesn't need to recompute an episode's numbers from scratch, and so accuracy can
be tracked across a season as more episodes are processed. Keyed by TVDB series id under
one JSON file per series -- suitable for the occasional read/write an analysis workflow
does, not the concurrent-write scale a real database would need.
"""
from __future__ import annotations

import json
from pathlib import Path


def _cache_path(cache_dir: str | Path, tvdb_id: int) -> Path:
    return Path(cache_dir) / f"tvdb-{tvdb_id}.json"


def load_series_cache(cache_dir: str | Path, tvdb_id: int) -> dict:
    path = _cache_path(cache_dir, tvdb_id)
    if not path.exists():
        return {"tvdb_id": tvdb_id, "episodes": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A corrupt/unreadable cache is treated exactly like a miss -- this is a
        # reproducible-from-source cache, never the sole copy of anything, so there is
        # nothing to recover and no reason to fail the caller over it.
        return {"tvdb_id": tvdb_id, "episodes": {}}


def record_episode_result(cache_dir: str | Path, tvdb_id: int, episode: int, result: dict) -> None:
    """Upserts one episode's metrics into the series cache and writes it back atomically
    (write-then-rename) so a crash mid-write can never leave a corrupt/partial cache file
    behind for the next reader to trip over."""
    path = _cache_path(cache_dir, tvdb_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    cache = load_series_cache(cache_dir, tvdb_id)
    cache["episodes"][str(episode)] = result
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)
