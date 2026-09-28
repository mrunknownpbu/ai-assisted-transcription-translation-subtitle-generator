"""Character names per episode, from TheTVDB, TMDB, IMDb and local .nfo.

Why per episode: a guest character's name can be an ordinary word in the
rest of the series. Real case (Love Is In The Air, 2026-09-28): "Deniz" is
the coffee-shop owner in S01E29-E37 and the word for "sea" in E01-E28;
all three services credit Deniz to exactly E29-E37. Knowing *where* a name
is a name is what lets cast_enrichment.py protect it without breaking the
common word elsewhere.

Sources (each optional; a missing key, an outage or no match only means
fewer credits, never an error):

* TMDB  -- TMDB_API_KEY. /tv/{id}/season/{s}/episode/{e}/credits: that
  episode's cast AND guest stars, with Turkish diacritics and nicknames
  ('Melek Yücel "Melo"').
* TVDB  -- tvdb_client (TVDB_API_KEY). /episodes/{id}/extended characters.
* IMDb  -- no API key: IMDb's official API is paid and scraping imdb.com is
  against its terms, so this reads the free non-commercial datasets
  (datasets.imdbws.com, title.episode + title.principals), streamed and
  filtered to one show, cached for CAST_CACHE_DAYS. Principals only
  (~10 per episode), names often without diacritics ("Eda Yildiz").
* TVmaze -- no key. Series cast (and per-episode guest cast where it has
  any); nicknames in parentheses ("Melek Yücel (Melo)").
* .nfo -- the series folder's tvshow.nfo <actor><role> list (Jellyfin/Kodi
  metadata): series regulars only, no episode numbers.

Credits merge on the folded GIVEN name (first word after dropping titles
like "Chef"; diacritics and Turkish dotted/dotless i ignored), because the
services disagree on surnames (TVDB "Deniz Saraçhan" vs TMDB/IMDb "Deniz
Karsu") but agree on what the characters call each other.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import httpx

_logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.environ.get("SUBTITLE_AI_CAST_CACHE", "/cache/cast"))
CAST_CACHE_DAYS = 30
TMDB_BASE = "https://api.themoviedb.org/3"
IMDB_DATASETS = "https://datasets.imdbws.com"
REQUEST_TIMEOUT = 15

# Titles in English-language credits ("Chef Alexander Zucco") and Turkish
# address forms that follow a name ("Fikret Bey") -- not part of the name
# the dialogue uses.
_PREFIX_TITLES = {"chef", "dr", "dr.", "doctor", "mr", "mr.", "mrs", "mrs.", "ms", "ms.", "miss",
                  "prof", "prof.", "professor", "officer", "detective", "captain", "uncle", "aunt",
                  "grandma", "grandpa", "young", "little", "old",
                  # Turkish titles before a name (TVmaze: "Şef Alexander Zucco"
                  # -- protecting "Şef", "chef/boss", would break the word).
                  "şef", "doktor", "hemşire", "komiser", "avukat", "müdür", "bay", "bayan",
                  "sayın", "hoca", "prens", "prenses", "kaptan", "profesör", "öğretmen"}
_SUFFIX_TITLES = {"bey", "hanım", "hanim", "abla", "abi", "ağabey", "teyze", "amca", "hoca", "efendi"}
_NOT_CHARACTERS = {"", "self", "himself", "herself", "themselves", "narrator", "voice", "guest",
                   "host", "various", "unknown", "extra", "additional voices"}
_PAREN_ANNOTATIONS = {"voice", "uncredited", "archive", "footage", "young", "younger", "child", "teen",
                      "adult", "old", "older", "credit", "only", "cameo", "flashback", "photo", "singing"}
_NICKNAME = re.compile(r"[\"“”'‘’]([^\"“”'‘’]+)[\"“”'‘’]")


def fold(text: str) -> str:
    """Case/diacritic-insensitive key: 'Pırıl' == 'Piril', 'İlker' == 'ilker'."""
    text = text.replace("ı", "i").replace("İ", "I")
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


@dataclass
class CastMember:
    given: str                                   # the name the dialogue uses, e.g. "Deniz"
    full_names: set[str] = field(default_factory=set)
    nicknames: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    episodes: set[tuple[int, int]] = field(default_factory=set)  # (season, episode)
    series_level: bool = False                   # credited without episode numbers (.nfo)
    _spellings: dict[str, int] = field(default_factory=dict, repr=False)

    def surface_forms(self) -> list[str]:
        forms = [self.given, *sorted(self.full_names), *sorted(self.nicknames)]
        seen, out = set(), []
        for f in forms:
            if f and fold(f) not in seen:
                seen.add(fold(f))
                out.append(f)
        return out


def parse_character(raw: str | None) -> tuple[str, str, list[str]] | None:
    """'Melek Yücel "Melo"' -> ("Melek", "Melek Yücel", ["Melo"]); None for
    non-character credits ("Self", "", "Narrator (voice)")."""
    if not raw:
        return None
    nicknames = [n.strip() for n in _NICKNAME.findall(raw) if n.strip()]
    name = _NICKNAME.sub(" ", raw)
    # TVmaze writes nicknames in parentheses ("Melek Yücel (Melo)"); other
    # sources use them for annotations ("(voice)", "(uncredited)").
    for inner in re.findall(r"\(([^)]*)\)", name):
        words = inner.split()
        if (1 <= len(words) <= 2 and words[0][:1].isupper()
                and not set(w.casefold() for w in words) & _PAREN_ANNOTATIONS):
            nicknames.append(inner.strip())
    name = re.sub(r"\([^)]*\)", " ", name)
    name = name.split("/")[0]                          # "Eda / Young Eda" -> first role
    words = [w for w in re.split(r"\s+", name.strip()) if w]
    while words and words[0].casefold() in _PREFIX_TITLES:
        words = words[1:]
    while words and words[-1].casefold() in _SUFFIX_TITLES:
        words = words[:-1]
    if not words or " ".join(words).casefold() in _NOT_CHARACTERS:
        return None
    if not words[0][0].isalpha():
        return None
    return words[0], " ".join(words), nicknames


class CastBook:
    """Accumulates credits from every source, merged by folded given name."""

    def __init__(self):
        self.members: dict[str, CastMember] = {}

    def add(self, raw: str | None, source: str, episode: tuple[int, int] | None = None) -> None:
        parsed = parse_character(raw)
        if not parsed:
            return
        given, full, nicknames = parsed
        key = fold(given)
        member = self.members.setdefault(key, CastMember(given=given))
        member._spellings[given] = member._spellings.get(given, 0) + 1
        # Prefer a spelling with real diacritics ("Pırıl" over IMDb's
        # "Piril"), then the most common one.
        member.given = max(member._spellings,
                           key=lambda s: (any(ord(c) > 127 for c in s), member._spellings[s]))
        if full != given:
            member.full_names.add(full)
        member.nicknames.update(n for n in nicknames if fold(n) != key)
        member.sources.add(source)
        if episode is None:
            member.series_level = True
        else:
            member.episodes.add(episode)

    def to_json(self) -> list[dict]:
        return [{"given": m.given, "full_names": sorted(m.full_names), "nicknames": sorted(m.nicknames),
                 "sources": sorted(m.sources), "episodes": sorted(m.episodes),
                 "series_level": m.series_level} for m in self.members.values()]


# --- cache ------------------------------------------------------------------

def _cached(key: str, fetch, max_age_days: float = CAST_CACHE_DAYS):
    path = CACHE_DIR / f"{key}.json"
    try:
        if path.is_file() and time.time() - path.stat().st_mtime < max_age_days * 86400:
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    value = fetch()
    if value is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return value


# --- TMDB -------------------------------------------------------------------

def _tmdb_get(path: str, **params):
    key = os.environ.get("TMDB_API_KEY", "").strip()
    if not key:
        return None
    try:
        resp = httpx.get(f"{TMDB_BASE}{path}", params={"api_key": key, **params}, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        _logger.info("TMDB %s unavailable: %s", path, exc)
        return None


def tmdb_ids(tvdb_id: int) -> tuple[int | None, str | None]:
    """(tmdb_id, imdb_id) for a TVDB series id, via TMDB's /find."""
    def fetch():
        found = _tmdb_get(f"/find/{tvdb_id}", external_source="tvdb_id")
        results = (found or {}).get("tv_results") or []
        if not results:
            return None
        tmdb_id = results[0]["id"]
        external = _tmdb_get(f"/tv/{tmdb_id}/external_ids") or {}
        return {"tmdb": tmdb_id, "imdb": external.get("imdb_id")}
    ids = _cached(f"ids-{tvdb_id}", fetch) or {}
    return ids.get("tmdb"), ids.get("imdb")


def add_tmdb(book: CastBook, tmdb_id: int) -> None:
    def fetch():
        show = _tmdb_get(f"/tv/{tmdb_id}")
        if not show:
            return None
        credits = []
        for season in show.get("seasons") or []:
            number = season.get("season_number")
            if not number:  # specials
                continue
            for ep in range(1, (season.get("episode_count") or 0) + 1):
                data = _tmdb_get(f"/tv/{tmdb_id}/season/{number}/episode/{ep}/credits")
                if data is None:
                    continue
                for person in (data.get("cast") or []) + (data.get("guest_stars") or []):
                    credits.append([person.get("character"), number, ep])
        return credits
    for character, season, ep in _cached(f"tmdb-{tmdb_id}", fetch) or []:
        book.add(character, "tmdb", (season, ep))


# --- TVDB -------------------------------------------------------------------

def add_tvdb(book: CastBook, tvdb_id: int) -> None:
    import tvdb_client
    if not tvdb_client.configured():
        return
    for ep in tvdb_client.series_episodes(tvdb_id):
        season, number, episode_id = ep.get("seasonNumber"), ep.get("number"), ep.get("id")
        if not season or not number or not episode_id:
            continue
        for character in tvdb_client.episode_characters(episode_id):
            if character.get("peopleType") in (None, "Actor", "Guest Star"):
                book.add(character.get("name"), "tvdb", (season, number))


# --- TVmaze (no key) --------------------------------------------------------

def add_tvmaze(book: CastBook, tvmaze_id: int) -> None:
    """Series cast (no episode numbers) plus per-episode guest cast where
    TVmaze has it (many non-English shows: none)."""
    def fetch():
        try:
            cast = httpx.get(f"https://api.tvmaze.com/shows/{tvmaze_id}/cast", timeout=REQUEST_TIMEOUT)
            episodes = httpx.get(f"https://api.tvmaze.com/shows/{tvmaze_id}/episodes",
                                 params={"embed": "guestcast"}, timeout=REQUEST_TIMEOUT)
            cast.raise_for_status()
            episodes.raise_for_status()
        except httpx.HTTPError as exc:
            _logger.info("TVmaze unavailable: %s", exc)
            return None
        credits = [[(c.get("character") or {}).get("name"), None, None] for c in cast.json()]
        for ep in episodes.json():
            for g in (ep.get("_embedded") or {}).get("guestcast") or []:
                credits.append([(g.get("character") or {}).get("name"), ep.get("season"), ep.get("number")])
        return credits
    for character, season, ep in _cached(f"tvmaze-{tvmaze_id}", fetch) or []:
        book.add(character, "tvmaze", (season, ep) if season else None)


# --- IMDb datasets ----------------------------------------------------------

def _stream_tsv(name: str):
    """Yield split rows of a gzipped IMDb dataset without storing it."""
    with httpx.stream("GET", f"{IMDB_DATASETS}/{name}", timeout=REQUEST_TIMEOUT,
                      follow_redirects=True) as resp:
        resp.raise_for_status()
        raw = io.BufferedReader(_ResponseReader(resp.iter_bytes()), buffer_size=1 << 20)
        with gzip.GzipFile(fileobj=raw) as gz:
            for line in io.TextIOWrapper(gz, encoding="utf-8"):
                yield line.rstrip("\n").split("\t")


class _ResponseReader(io.RawIOBase):
    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self._buf = b""

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        while not self._buf:
            try:
                self._buf = next(self._chunks)
            except StopIteration:
                return 0
        n = min(len(b), len(self._buf))
        b[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n


def add_imdb(book: CastBook, imdb_id: str) -> None:
    """~840MB streamed once per CAST_CACHE_DAYS per show (about 30s here),
    reduced to that show's own credits before caching."""
    def fetch():
        try:
            episodes = {row[0]: (int(row[2]), int(row[3]))
                        for row in _stream_tsv("title.episode.tsv.gz")
                        if len(row) >= 4 and row[1] == imdb_id and row[2].isdigit() and row[3].isdigit()}
            wanted = set(episodes) | {imdb_id}
            credits = []
            for row in _stream_tsv("title.principals.tsv.gz"):
                if len(row) >= 6 and row[0] in wanted and row[3] in ("actor", "actress"):
                    try:
                        characters = json.loads(row[5]) if row[5] != "\\N" else []
                    except ValueError:
                        continue
                    season_episode = episodes.get(row[0])
                    for character in characters:
                        credits.append([character, *(season_episode or (None, None))])
            return credits
        except (httpx.HTTPError, OSError, EOFError) as exc:
            _logger.info("IMDb datasets unavailable: %s", exc)
            return None
    for character, season, ep in _cached(f"imdb-{imdb_id}", fetch) or []:
        book.add(character, "imdb", (season, ep) if season is not None else None)


# --- local .nfo -------------------------------------------------------------

def add_nfo(book: CastBook, series_root: Path) -> None:
    nfo = series_root / "tvshow.nfo"
    try:
        root = ET.parse(nfo).getroot()
    except (OSError, ET.ParseError):
        return
    for actor in root.iter("actor"):
        book.add(actor.findtext("role"), "nfo", None)


def nfo_ids(series_root: Path) -> dict[str, str]:
    try:
        root = ET.parse(series_root / "tvshow.nfo").getroot()
    except (OSError, ET.ParseError):
        return {}
    return {u.get("type"): (u.text or "").strip() for u in root.iter("uniqueid") if u.get("type")}


def fetch_cast(tvdb_id: int, series_root: Path | None = None, *,
               sources: tuple[str, ...] = ("tmdb", "tvdb", "tvmaze", "imdb", "nfo")) -> CastBook:
    """IDs come from Sonarr first (arr_client -- it knows TMDB, IMDb and
    TVmaze ids for every series), then the series' tvshow.nfo, then TMDB's
    /find by TVDB id."""
    import arr_client
    book = CastBook()
    ids: dict = {}
    if series_root:
        info = arr_client.lookup(str(series_root), ("series",))
        if info and info.tvdb_id == tvdb_id:
            ids.update(info.ids)
        local = nfo_ids(series_root)
        if local.get("tmdb", "").isdigit():
            ids.setdefault("tmdb", int(local["tmdb"]))
        if local.get("imdb"):
            ids.setdefault("imdb", local["imdb"])
        if local.get("tvmaze", "").isdigit():
            ids.setdefault("tvmaze", int(local["tvmaze"]))
    if ("tmdb" in sources or "imdb" in sources) and not (ids.get("tmdb") and ids.get("imdb")):
        tmdb_id, imdb_id = tmdb_ids(tvdb_id)
        ids.setdefault("tmdb", tmdb_id)
        ids.setdefault("imdb", imdb_id)
    if "tmdb" in sources and ids.get("tmdb"):
        add_tmdb(book, ids["tmdb"])
    if "tvdb" in sources:
        add_tvdb(book, tvdb_id)
    if "tvmaze" in sources and ids.get("tvmaze"):
        add_tvmaze(book, ids["tvmaze"])
    if "imdb" in sources and ids.get("imdb"):
        add_imdb(book, ids["imdb"])
    if "nfo" in sources and series_root:
        add_nfo(book, series_root)
    return book


def fetch_movie_cast(tmdb_id: int, movie_dir: Path | None = None) -> CastBook:
    """A movie's characters: TMDB's full movie credits plus the folder's
    .nfo roles. No episode numbers, so every credit is film-wide. IMDb is
    not used for movies: its dataset has only ~10 principals per title
    (TMDB lists the whole cast) and would cost a 784MB stream per movie."""
    book = CastBook()

    def fetch():
        data = _tmdb_get(f"/movie/{tmdb_id}/credits")
        return None if data is None else [c.get("character") for c in data.get("cast") or []]
    for character in _cached(f"tmdb-movie-{tmdb_id}", fetch) or []:
        book.add(character, "tmdb", None)
    if movie_dir:
        for nfo in sorted(Path(movie_dir).glob("*.nfo")):
            try:
                root = ET.parse(nfo).getroot()
            except (OSError, ET.ParseError):
                continue
            for actor in root.iter("actor"):
                book.add(actor.findtext("role"), "nfo", None)
    return book
