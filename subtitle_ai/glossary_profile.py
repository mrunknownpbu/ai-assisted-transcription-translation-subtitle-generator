"""Loads the layered glossary YAML profiles (global -> category -> series,
increasing precedence) into glossary.Entity objects for build_glossary().
Format is unchanged from the prior implementation's validated design
(real data: /glossary/*.yaml) -- this module only loads and merges it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from glossary import Entity, PhraseEntry, _phrase_key

# Matches the {tvdb-<id>} tag anywhere in a path -- it normally lives on an
# ancestor directory (the series folder), not the episode filename itself.
_TVDB_PATTERN = re.compile(r"\{tvdb-(\d+)\}", re.IGNORECASE)


def find_tvdb_id(path: str) -> int | None:
    m = _TVDB_PATTERN.search(path)
    return int(m.group(1)) if m else None


def find_series_root(path: str) -> Path | None:
    """The series folder itself (e.g. `Show (2020) {tvdb-383383}/`),
    not just its id -- used by auto_glossary.py to know where to look
    for a series' other already-completed episodes. Returns None under
    exactly the same condition find_tvdb_id() would (no {tvdb-<id>}
    anywhere in the path), so callers can treat "no match" identically
    for both."""
    for parent in Path(path).parents:
        if _TVDB_PATTERN.search(parent.name):
            return parent
    return None


@dataclass
class Profile:
    tvdb_id: int | None
    title: str | None
    title_source: str = "none"  # "local" | "tvdb" | "none" -- where `title` came from
    sources: list[str] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    phrases: list[PhraseEntry] = field(default_factory=list)


def _load_yaml_entities(path: Path) -> tuple[dict | None, list[dict]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data, list(data.get("entities", []))


def find_series_glossary_path(glossary_dir: str | Path, tvdb_id: int) -> Path | None:
    """Scans every `*.yaml` under glossary_dir for the one file whose own
    `tvdb_id` key matches -- the same content-addressed rule load_profile()
    itself follows below (never by filename convention). Returns None if
    this series has no series-specific glossary file yet -- a completely
    normal, common state, not an error (see api.py's promote endpoint,
    which creates one on first use rather than requiring it pre-exist)."""
    directory = Path(glossary_dir)
    for path in sorted(directory.glob("*.yaml")):
        data, _ = _load_yaml_entities(path)
        if data and data.get("tvdb_id") == tvdb_id:
            return path
    return None


def load_profile(glossary_dir: str | Path, tvdb_id: int | None = None, *,
                 enrich_from_tvdb: bool = True) -> Profile:
    """Layering is decided by content, never by filename convention: a
    file with no `tvdb_id` key is a global/category layer and always
    applies; a file that DOES declare one is series-specific and applies
    only when it matches the requested `tvdb_id` -- never by pattern-
    matching the filename. (Real bug this fixes: an earlier version
    classified files as "category" whenever their name didn't contain
    "global", which let an unrelated series' glossary leak in whenever no
    tvdb_id was requested or none matched -- caught by
    test_series_layer_excluded_when_tvdb_id_does_not_match.)

    Series-specific entries are applied last, so they override a
    same-named canonical entry from an earlier layer."""
    directory = Path(glossary_dir)
    sources: list[str] = []
    by_canonical: dict[str, dict] = {}
    by_phrase_key: dict[str, dict] = {}
    title = None

    layered: list[Path] = []
    for path in sorted(directory.glob("*.yaml")):
        data, _ = _load_yaml_entities(path)
        declared_tvdb_id = data.get("tvdb_id") if data else None
        if declared_tvdb_id is None:
            layered.append(path)
    series_layer = find_series_glossary_path(directory, tvdb_id) if tvdb_id is not None else None

    ordered = layered + ([series_layer] if series_layer else [])
    for path in ordered:
        data, entities = _load_yaml_entities(path)
        if path == series_layer:
            title = data.get("title")
        for e in entities:
            by_canonical[e["canonical"]] = e
        # A file's `language:` key (if any) applies to every phrase it
        # declares -- a whole file IS "the Turkish glossary", not
        # individual per-entry tags. Later layers (series wins) override
        # an earlier layer's phrase for the same normalized key, exactly
        # mirroring by_canonical above.
        file_language = data.get("language") if data else None
        for p in (data.get("phrases", []) if data else []):
            by_phrase_key[_phrase_key(p["source"])] = {**p, "language": file_language}
        sources.append(path.name)

    entities = [Entity(canonical=e["canonical"], surface_forms=[e["canonical"], *e.get("aliases", [])])
               for e in by_canonical.values() if e.get("protected")]
    phrases = [PhraseEntry(source=p["source"], translation=p["translation"], language=p.get("language"))
              for p in by_phrase_key.values()]
    title_source = "local" if title else "none"

    # TVDB enrichment is metadata-only and best-effort: a missing key, a
    # network outage, or no match never fails profile loading -- the local
    # glossary (already loaded above) is always sufficient on its own.
    # Lazily imported so a job with no configured TVDB_API_KEY never pays
    # even the import cost.
    if title is None and tvdb_id is not None and enrich_from_tvdb:
        import tvdb_client
        record = tvdb_client.series(tvdb_id)
        if record and record.get("name"):
            title = record["name"]
            title_source = "tvdb"

    return Profile(tvdb_id=tvdb_id, title=title, title_source=title_source,
                   sources=sources, entities=entities, phrases=phrases)
