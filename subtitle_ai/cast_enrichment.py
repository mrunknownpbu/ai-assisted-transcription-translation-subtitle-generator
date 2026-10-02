"""Evidence-gated, episode-scoped protection of character names pulled
from cast metadata (cast_metadata.py).

This automates the project's evidence bar for protecting a name (CLAUDE.md
"Entity-protection precedent"), which until now was applied by hand:

1. Recurrence -- the name is used AS A NAME in the series' own source
   subtitles: >= MIN_NAME_LINES lines across >= MIN_NAME_EPISODES of the
   episodes it is credited in. "As a name" is deliberately strict
   (name_shaped()): mid-sentence capitalised, followed by an apostrophe
   suffix (Deniz'i), or addressed (Deniz, / Deniz!). A capital at the start
   of a sentence alone does not count -- "Deniz kenarında..." (by the sea),
   "Can" (soul), "Sevda" (love) are ordinary words there.
2. A confirmed mistranslation -- those lines, translated by the production
   engine WITHOUT protection, lose the name in >= MIN_PROBE_FAILURES lines
   and >= MIN_PROBE_FAILURE_RATE of them (Deniz'e âşık mısın? -> "Are you in
   love with the sea?").

A name passing both is written to the series glossary as protected,
scoped to the episodes it is credited in (plus SCOPE_MARGIN, for mentions
just after a character leaves) unless it is a regular, and committed to
the glossary repo with its evidence. Entries a person wrote are never
modified -- the report only flags them (e.g. a series-wide entry for a
name metadata credits to a few episodes). Every candidate's outcome,
passed or not, goes to a JSON report the Series page shows.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cast_metadata
import glossary_files
import glossary_profile
from cast_metadata import CastMember, fold

_logger = logging.getLogger(__name__)

MIN_NAME_LINES = 5
MIN_NAME_EPISODES = 2
MIN_PROBE_FAILURES = 2
MIN_PROBE_FAILURE_RATE = 0.10
PROBE_LINES = 30
SCOPE_MARGIN = 3          # episodes after the last credit that stay protected
REGULAR_SHARE = 0.6       # credited in >= 60% of known episodes -> series-wide
_LETTER = r"A-Za-zÇĞİÖŞÜÂÎÛçğıöşüâîû"
_CJK_CHAR = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ가-힯]")
_KATAKANA = re.compile(r"^[ァ-ンー]+$")
_HANGUL = re.compile(r"^[가-힣]+$")
_SUBTITLE_MODIFIERS = {"hi", "sdh", "cc", "forced"}
_SENTENCE_START = re.compile(r"(^|[.!?…\"“”\-–—:]\s*)$")


@dataclass
class Candidate:
    member: CastMember
    scope: list[str] | None = None            # None = series-wide
    forms: list[str] = field(default_factory=list)
    name_lines: list[str] = field(default_factory=list)
    name_episodes: set[tuple[int, int]] = field(default_factory=set)
    probe_total: int = 0
    probe_failures: list[tuple[str, str]] = field(default_factory=list)
    decision: str = ""
    reason: str = ""


# --- source subtitles -------------------------------------------------------

MOVIE_EPISODE = (0, 0)  # a movie's subtitles, keyed like one episode


def source_subtitles(series_root: Path, *, movie: bool = False) -> tuple[str | None, dict[tuple[int, int], list[str]]]:
    """(language, {(season, episode): [cue text, ...]}) from the series'
    original-language `<stem>.<lang>.srt` files -- the most common non-English
    language code wins."""
    from srt import parse
    by_lang: dict[str, dict[tuple[int, int], Path]] = {}
    for path in series_root.rglob("*.srt"):
        parts = [p.lower() for p in path.name.split(".")]
        if len(parts) < 3 or not (2 <= len(parts[-2]) <= 3) or not parts[-2].isalpha():
            continue
        # `.en.hi.srt` is English for the hearing impaired, not Hindi: a
        # modifier after a language code marks a variant file -- skip it.
        if parts[-2] in _SUBTITLE_MODIFIERS and len(parts) >= 4 and 2 <= len(parts[-3]) <= 3:
            continue
        lang = parts[-2]
        episode = MOVIE_EPISODE if movie else glossary_profile.find_episode(path.name)
        if lang == "en" or episode is None:
            continue
        by_lang.setdefault(lang, {})[episode] = path
    if not by_lang:
        return None, {}
    lang = max(by_lang, key=lambda code: len(by_lang[code]))
    texts = {}
    for episode, path in by_lang[lang].items():
        try:
            cues = parse(path)
        except (OSError, ValueError):
            continue
        # The file name can lie: a bare ".hi.srt" is Hindi by the naming
        # convention but English-for-the-hearing-impaired in some releases
        # (found 2026-09-28: Veer-Zaara's ".hi.srt" is English). Probing
        # English lines as Hindi would measure nothing, so only files whose
        # text really is `lang` count.
        if _text_language(cues) != lang:
            _logger.info("skipping %s: its text is not %r", path.name, lang)
            continue
        texts[episode] = [c.text.replace("\n", " ") for c in cues]
    return (lang if texts else None), texts


def source_subtitle_episode_count(series_root: Path) -> int:
    """Cheap upper bound for new source-subtitle evidence.

    This deliberately reads filenames only: it runs during every idle cast
    sweep for reports that missed the multi-episode evidence gate, while
    parsing and language-validating subtitle text belongs to enrichment
    itself. A mislabeled new file can cause one extra enrichment, never a
    repeated external refresh once its report is saved.
    """
    by_lang: dict[str, set[tuple[int, int]]] = {}
    for path in series_root.rglob("*.srt"):
        parts = [part.lower() for part in path.name.split(".")]
        if len(parts) < 3 or not (2 <= len(parts[-2]) <= 3) or not parts[-2].isalpha():
            continue
        if parts[-2] in _SUBTITLE_MODIFIERS and len(parts) >= 4 and 2 <= len(parts[-3]) <= 3:
            continue
        episode = glossary_profile.find_episode(path.name)
        if parts[-2] != "en" and episode is not None:
            by_lang.setdefault(parts[-2], set()).add(episode)
    return max((len(episodes) for episodes in by_lang.values()), default=0)


def _text_language(cues) -> str:
    from srt_translation import _detect_source_language
    detected, _probability = _detect_source_language(cues)
    return detected


def name_shaped(text: str, form: str) -> bool:
    """True if `form` occurs in `text` as a name (see module docstring)."""
    if not form or not text:
        return False
    if _CJK_CHAR.search(form):
        if _KATAKANA.match(form):
            return bool(re.search(rf"(?<![ァ-ンー]){re.escape(form)}(?![ァ-ンー])", text))
        elif _HANGUL.match(form):
            return bool(re.search(rf"(?<![가-힣]){re.escape(form)}", text))
        else:
            return form in text

    pattern = re.compile(rf"(?<![{_LETTER}]){re.escape(form)}(?![{_LETTER}])")
    for m in pattern.finditer(text):
        after = text[m.end():m.end() + 2]
        if after[:1] in ("'", "’") and after[1:2].isalpha():
            return True                                   # Deniz'i, Deniz'e
        if after[:1] in (",", "!", "?"):
            return True                                   # Deniz, / Deniz!
        if not _SENTENCE_START.search(text[:m.start()]):
            return True                                   # mid-sentence capital
    return False


# --- scope ------------------------------------------------------------------

def scope_for(member: CastMember, known: dict[int, int]) -> list[str] | None:
    """`known`: {season: last episode number}. None = series-wide."""
    total = sum(known.values()) or 1
    if member.series_level and not member.episodes:
        return None
    if len(member.episodes) >= REGULAR_SHARE * total:
        return None
    scope = []
    for season in sorted({s for s, _ in member.episodes}):
        numbers = sorted(e for s, e in member.episodes if s == season)
        last = min(numbers[-1] + SCOPE_MARGIN, known.get(season, numbers[-1] + SCOPE_MARGIN))
        scope.append(f"S{season:02d}E{numbers[0]:02d}-E{last:02d}")
    return scope


def _in(scope: list[str] | None, episode: tuple[int, int]) -> bool:
    return glossary_profile.in_scope(glossary_profile.parse_episode_scope(scope), episode)


# --- probe ------------------------------------------------------------------

def default_probe(lines: list[str], lang: str) -> list[str]:
    """Translate `lines` exactly as production would, minus protection."""
    import translate
    cues = [SimpleNamespace(text=line) for line in lines]
    return translate.translate_spans(cues, [[i] for i in range(len(cues))], lang)


def _keeps_name(output: str, member: CastMember) -> bool:
    folded = fold(output)
    return any(re.search(rf"(?<![a-z]){re.escape(fold(f))}(?![a-z])", folded)
               for f in member.surface_forms())


def _sample(items: list[str], n: int) -> list[str]:
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


# --- glossary ---------------------------------------------------------------

def _index_entity_forms(entities: list[dict]) -> dict[str, dict]:
    return {
        fold(str(form)): entry
        for entry in entities
        for form in [entry.get("canonical"), *(entry.get("aliases") or [])]
        if form
    }


def _existing_forms(glossary_dir: Path, key_field: str, key: int) -> dict[str, dict]:
    """{folded surface form: entry} over every layer that applies to this series/movie."""
    forms: dict[str, dict] = {}
    for path in sorted(glossary_dir.glob("*.yaml")):
        data = glossary_files.load(path)
        own = data.get(key_field) == key
        if not own and any(data.get(f) is not None for f in glossary_profile.KEY_FIELDS):
            continue
        forms.update(_index_entity_forms(data.get("entities") or []))
    return forms


def enrich_series(tvdb_id: int, series_root: Path, glossary_dir: Path, *, probe=default_probe,
                  book: cast_metadata.CastBook | None = None, dry_run: bool = False) -> dict:
    """Fetch a series' cast, gather evidence, protect what passes. Returns the report."""
    book = book or cast_metadata.fetch_cast(tvdb_id, series_root)
    lang, subtitles = source_subtitles(series_root)
    return _enrich("tvdb_id", tvdb_id, f"tvdb-{tvdb_id}", book, lang, subtitles, Path(glossary_dir),
                   probe=probe, dry_run=dry_run, min_episodes=MIN_NAME_EPISODES)


def enrich_movie(tmdb_id: int, movie_dir: Path, glossary_dir: Path, *, imdb_id: str | None = None,
                 probe=default_probe, book: cast_metadata.CastBook | None = None,
                 dry_run: bool = False) -> dict:
    """The movie version: cast from TMDB movie credits (+ the movie's .nfo),
    subtitles from the movie's folder, protection film-wide, and the
    recurrence gate relaxed to the one film (no episodes to span)."""
    book = book or cast_metadata.fetch_movie_cast(tmdb_id, movie_dir)
    lang, subtitles = source_subtitles(movie_dir, movie=True)
    return _enrich("tmdb_movie_id", tmdb_id, f"movie-{tmdb_id}", book, lang, subtitles, Path(glossary_dir),
                   probe=probe, dry_run=dry_run, min_episodes=1)


def _enrich(key_field: str, key: int, report_key: str, book, lang, subtitles, glossary_dir: Path, *,
            probe, dry_run: bool, min_episodes: int) -> dict:
    started = time.time()
    report = {"key": report_key, key_field: key, "checked_at": started, "language": lang,
              "episodes_with_subtitles": len(subtitles), "candidates": [], "added": [], "flags": []}
    if not book.members or not subtitles or not lang:
        report["note"] = "no cast metadata or no source subtitles"
        return report

    known: dict[int, int] = {}
    for member in book.members.values():
        for season, number in member.episodes:
            known[season] = max(known.get(season, 0), number)
    for season, number in subtitles:
        known[season] = max(known.get(season, 0), number)

    existing = _existing_forms(glossary_dir, key_field, key)
    candidates: list[Candidate] = []
    for member in book.members.values():
        cand = Candidate(member=member, scope=scope_for(member, known), forms=member.surface_forms())
        candidates.append(cand)
        entry = existing.get(fold(member.given))
        if entry is not None and entry.get("source") != "metadata":
            cand.decision, cand.reason = "skip", "already in the glossary (entry written by a person)"
            if entry.get("episodes") is None and cand.scope is not None:
                report["flags"].append(
                    f"{entry.get('canonical')}: protected series-wide, but cast metadata credits it only "
                    f"to {', '.join(cand.scope)} -- consider adding that `episodes:` scope")
            continue
        forms = [f for f in cand.forms if f[:1].isupper() or any(ord(c) > 127 for c in f)]
        for episode, lines in subtitles.items():
            if not _in(cand.scope, episode):
                continue
            for line in lines:
                if any(name_shaped(line, f) for f in forms):
                    cand.name_lines.append(line)
                    cand.name_episodes.add(episode)
        if len(cand.name_lines) < MIN_NAME_LINES or len(cand.name_episodes) < min_episodes:
            cand.decision = "skip"
            cand.reason = (f"used as a name in {len(cand.name_lines)} lines / {len(cand.name_episodes)} "
                           f"episodes (needs {MIN_NAME_LINES} / {min_episodes})")

    to_probe = [c for c in candidates if not c.decision]
    batches = [(c, _sample(sorted(set(c.name_lines)), PROBE_LINES)) for c in to_probe]
    flat = [line for _, lines in batches for line in lines]
    outputs = probe(flat, lang) if flat else []
    cursor = 0
    for cand, lines in batches:
        outs = outputs[cursor:cursor + len(lines)]
        cursor += len(lines)
        cand.probe_total = len(lines)
        cand.probe_failures = [(src, out) for src, out in zip(lines, outs) if not _keeps_name(out, cand.member)]
        n = len(cand.probe_failures)
        if n >= MIN_PROBE_FAILURES and n >= MIN_PROBE_FAILURE_RATE * cand.probe_total:
            cand.decision = "protect"
            cand.reason = f"unprotected translation lost the name in {n}/{cand.probe_total} lines"
        else:
            cand.decision = "skip"
            cand.reason = f"translates correctly unprotected ({n}/{cand.probe_total} lines lost it)"

    changed: list[Candidate] = []
    candidate_reports = {}
    for cand in candidates:
        m = cand.member
        candidate_report = {
            "name": m.given, "full_names": sorted(m.full_names), "nicknames": sorted(m.nicknames),
            "sources": sorted(m.sources), "credited_episodes": len(m.episodes), "scope": cand.scope,
            "name_lines": len(cand.name_lines), "name_episodes": len(cand.name_episodes),
            "probe": f"{len(cand.probe_failures)}/{cand.probe_total}" if cand.probe_total else None,
            "examples": [{"source": s, "unprotected": o} for s, o in cand.probe_failures[:3]],
            "decision": cand.decision, "reason": cand.reason}
        report["candidates"].append(candidate_report)
        candidate_reports[m.given] = candidate_report
        if cand.decision == "protect":
            changed.append(cand)

    report["added"] = [cand.member.given for cand in changed]
    if changed and not dry_run:
        with glossary_files.edit_lock(glossary_dir):
            series_path = glossary_profile.find_glossary_path(glossary_dir, key_field, key)
            series_data: dict[str, Any]
            if series_path is None:
                series_path = glossary_dir / f"{report_key}.yaml"
                series_data = {key_field: key, "title": None, "entities": []}
            else:
                series_data = glossary_files.load(series_path)
            entities = series_data.setdefault("entities", [])
            latest_forms = _index_entity_forms(entities)
            added = []
            for cand in changed:
                m = cand.member
                person_entry = next((latest_forms[fold(form)] for form in cand.forms
                                     if fold(form) in latest_forms
                                     and latest_forms[fold(form)].get("source") != "metadata"), None)
                if person_entry is not None:
                    cand.decision = "skip"
                    cand.reason = "added to the glossary by a person while cast metadata was being checked"
                    candidate_report = candidate_reports[m.given]
                    candidate_report["decision"] = cand.decision
                    candidate_report["reason"] = cand.reason
                    continue
                # case_sensitive: these are names that are often also words
                # (Melek = angel, Kiraz = cherry); only the capitalised name
                # is protected, never the lowercase word.
                aliases = [form for form in cand.forms[1:]
                           if fold(form) not in latest_forms
                           or latest_forms[fold(form)].get("source") == "metadata"]
                entry = {"canonical": m.given, "aliases": aliases, "protected": True, "case_sensitive": True}
                if cand.scope:
                    entry["episodes"] = cand.scope
                entry["source"] = "metadata"
                entry["evidence"] = {
                    "credited_by": sorted(m.sources), "credited_episodes": len(m.episodes),
                    "name_lines": len(cand.name_lines), "name_episodes": len(cand.name_episodes),
                    "unprotected_probe": f"{len(cand.probe_failures)}/{cand.probe_total} lines lost the name",
                    "checked": date.fromtimestamp(started).isoformat()}
                previous = next((e for e in entities if e.get("source") == "metadata"
                                 and fold(str(e.get("canonical", ""))) == fold(m.given)), None)
                if previous is not None:
                    previous.update(entry)
                    target = previous
                else:
                    entities.append(entry)
                    target = entry
                for form in cand.forms:
                    latest_forms[fold(form)] = target
                added.append(m.given)
            report["added"] = added
            if added:
                glossary_files.write(series_path, series_data)
                glossary_files.commit(series_path, "Protect " + ", ".join(repr(n) for n in added)
                                      + f" ({report_key}) from cast metadata")
    report["seconds"] = round(time.time() - started, 1)
    return report


# --- report / scheduling ----------------------------------------------------

def _report_key(key) -> str:
    """A tvdb id (series, the original form) or a "movie-<tmdb>" key."""
    return f"tvdb-{key}" if isinstance(key, int) else str(key)


def report_path(key) -> Path:
    name = _report_key(key)
    # Series reports keep their original file name (report-<tvdb>.json).
    return cast_metadata.CACHE_DIR / (f"report-{key}.json" if isinstance(key, int) else f"report-{name}.json")


def save_report(report: dict) -> None:
    key = report.get("tvdb_id") if report.get("tvdb_id") is not None else report["key"]
    path = report_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


def load_report(key) -> dict | None:
    try:
        return json.loads(report_path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def refresh_days() -> float:
    """SUBTITLE_AI_CAST_REFRESH_DAYS (default 0.5 / 12 hours; 0 disables automatic runs)."""
    raw = os.environ.get("SUBTITLE_AI_CAST_REFRESH_DAYS", "").strip()
    try:
        return max(0.0, float(raw)) if raw else 0.5
    except ValueError:
        return 0.5


def is_stale(key, now: float | None = None) -> bool:
    """`key`: a series' tvdb id, or "movie-<tmdb>"."""
    days = refresh_days()
    if days <= 0:
        return False
    report = load_report(key)
    return report is None or ((now or time.time()) - report.get("checked_at", 0)) > days * 86400


def needs_evidence_refresh(key, series_root: Path, now: float | None = None) -> bool:
    """Whether newly arrived subtitle episodes justify an early re-check.

    Only reports that were blocked by the recurrence gate qualify. This keeps
    the normal 30-day cadence for reports that already had enough evidence,
    including ones that simply found no mistranslation worth protecting.
    """
    if is_stale(key, now):
        return True
    report = load_report(key)
    if not report:
        return True
    candidates = report.get("candidates") or []
    insufficient = any(
        candidate.get("decision") == "skip"
        and candidate.get("name_lines", 0) >= MIN_NAME_LINES
        and candidate.get("name_episodes", 0) < MIN_NAME_EPISODES
        for candidate in candidates
    )
    return insufficient and source_subtitle_episode_count(series_root) > report.get("episodes_with_subtitles", 0)
