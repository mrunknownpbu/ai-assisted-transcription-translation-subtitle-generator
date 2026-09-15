"""Loads layered glossary YAML profiles (global -> category -> series, increasing
precedence) into glossary.Entity objects. Ported from a sibling project's design
(subtitle-ai's glossary_profile.py), whose real, human-curated data for at least one show
directly confirmed a bug found independently this session: an "Evren" -> "Mr. Universe"
mistranslation, documented there with the exact same failure text, because "Evren" (a
minor/supporting character) isn't in TheTVDB's own cast list for that series at all. Live
TVDB cast lookup (tvdb_client.glossary_from_characters) is automatic and needs zero setup,
but can't know about a character TVDB never catalogued, or that a show's dialogue actually
uses a nickname ("Melo") instead of the full name TVDB gives ("Melek Yücel") -- this layer
is for exactly that: a human-reviewed correction file per series, checked into
per-deployment config rather than fetched live.

File format (unchanged from the source project, so existing curated files work as-is):
    tvdb_id: 383383          # omit entirely for a global/category (always-applies) layer
    title: "Show Name"       # optional, series layer only
    entities:
      - canonical: Eda Yıldız
        protected: true      # false/omitted = documentation only, never substituted
        aliases: [Eda Yildiz, Eda]
        notes: "..."          # human-readable rationale, not consumed by the loader
"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.pipeline.stage8_translation.glossary import Entity


def load_profile_entities(glossary_dir: str | Path, tvdb_id: int | None) -> list[Entity]:
    """Layering is decided by content, never by filename convention: a file with no
    `tvdb_id` key is a global/category layer and always applies; a file that DOES declare
    one is series-specific and applies only when it matches the requested `tvdb_id`. Real
    bug this avoids (documented in the source project): classifying files as "category"
    by filename let an unrelated series' glossary leak in whenever no tvdb_id matched.

    Series-specific entries are loaded last, so they override a same-named canonical entry
    from an earlier (global/category) layer. Missing directory, unreadable YAML, or no
    matching series file all just yield no entities -- this is an optional enrichment
    layer, never a hard dependency for translation to proceed."""
    directory = Path(glossary_dir)
    if not directory.is_dir():
        return []

    layered: list[Path] = []
    series_layer: Path | None = None
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        declared_tvdb_id = data.get("tvdb_id")
        if declared_tvdb_id is None:
            layered.append(path)
        elif tvdb_id is not None and declared_tvdb_id == tvdb_id:
            series_layer = path

    by_canonical: dict[str, dict] = {}
    for path in layered + ([series_layer] if series_layer else []):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for e in data.get("entities", []):
            by_canonical[e["canonical"]] = e

    return [
        Entity(canonical=e["canonical"], surface_forms=[e["canonical"], *e.get("aliases", [])])
        for e in by_canonical.values() if e.get("protected")
    ]
