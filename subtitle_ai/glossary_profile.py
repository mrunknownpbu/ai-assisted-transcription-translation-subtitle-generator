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

from glossary import Entity

# Matches the {tvdb-<id>} tag anywhere in a path -- it normally lives on an
# ancestor directory (the series folder), not the episode filename itself.
_TVDB_PATTERN = re.compile(r"\{tvdb-(\d+)\}", re.IGNORECASE)


def find_tvdb_id(path: str) -> int | None:
    m = _TVDB_PATTERN.search(path)
    return int(m.group(1)) if m else None


@dataclass
class Profile:
    tvdb_id: int | None
    title: str | None
    title_source: str = "none"  # "local" | "tvdb" | "none" -- where `title` came from
    sources: list[str] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)


def _load_yaml_entities(path: Path) -> tuple[dict | None, list[dict]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data, list(data.get("entities", []))


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
    title = None

    layered: list[Path] = []
    series_layer: Path | None = None
    for path in sorted(directory.glob("*.yaml")):
        data, _ = _load_yaml_entities(path)
        declared_tvdb_id = data.get("tvdb_id") if data else None
        if declared_tvdb_id is None:
            layered.append(path)
        elif tvdb_id is not None and declared_tvdb_id == tvdb_id:
            series_layer = path

    ordered = layered + ([series_layer] if series_layer else [])
    for path in ordered:
        data, entities = _load_yaml_entities(path)
        if path == series_layer:
            title = data.get("title")
        for e in entities:
            by_canonical[e["canonical"]] = e
        sources.append(path.name)

    entities = [Entity(canonical=e["canonical"], surface_forms=[e["canonical"], *e.get("aliases", [])])
               for e in by_canonical.values() if e.get("protected")]
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
                   sources=sources, entities=entities)
