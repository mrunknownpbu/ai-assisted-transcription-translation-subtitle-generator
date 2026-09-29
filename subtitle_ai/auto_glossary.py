"""Auto-mines a series' own already-completed sibling episode outputs
for recurring proper names, so a per-series glossary doesn't have to be
hand-authored from scratch (see /glossary/*.yaml, glossary_profile.py).

Confirmed real need (Love Is In The Air audit, 2026-09-17): the manually
curated glossary for that series was built by eyeballing one episode's
transcript and picking out recurring names by hand -- a one-off chore
that has to be redone per series and misses names that only become
obviously recurring once several episodes exist.

Safety framing (deliberate, load-bearing design decision -- see
worker.py's Worker._load_auto_hotwords and pipeline.py's `run()`):
names found here feed ASR `hotwords` ONLY, never translation-time
protect()/restore() (glossary.build_glossary()). A false-positive
hotword is a mild decoding bias with no correctness risk; a false-
positive PROTECTED entity would silently corrupt genuine dialogue
translation with no human review. Promoting a mined name to actual
translation protection is a human decision, made by copying it from
this module's write_suggestions() output into the real, hand-curated
/glossary YAML (which stays the only path to protect()/restore()).

Per-language mining (2026-09-29): v1 was Turkish-source-only, with a
single hardcoded capitalization pattern. Real orthographic signal
differs by script, so each language below is a separate LanguageMiner
registered in MINERS, not one pattern stretched to fit every script:
- tr, ms: Latin capitalization (Turkish/Malay orthography capitalizes
  proper nouns like English does) -- a sentence-initial capital is a
  confound (ordinary sentence-initial capitalization looks identical),
  resolved the same way as before: a candidate only counts once also
  seen capitalized somewhere NOT cue-initial ("corroboration").
- ja: katakana script-switching. Japanese has no capitalization at all,
  but katakana (vs. the default hiragana/kanji prose) is a real,
  distinct-script signal genuinely used for foreign names, loanwords,
  and stylized native names -- confirmed against real production data
  (2026-09-29, "Happy Kanako's Killer Life" S02E01-03 + "Hammer
  Session!" S01E01, the only real .ja.srt transcripts in this
  deployment): correctly surfaces real character names (カナコ/Kanako,
  カズ/Kazu, ユイ/Yui) and, notably, カツール an agency name (スマイル/
  "Smile") that a real mistranslation bug traced back to -- see
  CLAUDE.md's dated entry. No script-based confound exists the way
  sentence-initial capitalization does, so every occurrence
  self-corroborates (position doesn't matter).
- ko, zh, th NOT implemented: Hangul/Hanzi/Thai script have no
  capitalization-equivalent marker at all, and (checked 2026-09-29) this
  deployment has zero real source-language transcripts in any of the
  three to validate a frequency-based alternative against -- shipping an
  unvalidated heuristic for languages this project has no real evidence
  about would repeat exactly the mistake docs/turkish-language-support.md
  documents choosing NOT to make. mine_series_entities()/
  mine_series_entities_auto() both degrade to [] for any unregistered
  language, same as today's "no match of any kind" behavior -- add a
  MINERS entry (see LanguageMiner's docstring) once real transcribed
  episodes exist to measure a heuristic against, the same discipline
  every other threshold in this codebase was built with.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import yaml

import glossary_files
import srt

SOURCE_LANG = "tr"

# ---------------------------------------------------------------------------
# Turkish (tr): Latin capitalization.
# ---------------------------------------------------------------------------

# Matches faster-whisper's own Turkish capitalization behavior for
# proper nouns: a leading uppercase letter (ASCII or Turkish-specific),
# rest lowercase. ALL-CAPS is excluded on purpose -- Whisper occasionally
# emits emphasis/acronym-shaped artifacts in all-caps, which is never a
# genuine name.
_LATIN_PROPER_NOUN_TR = re.compile(r"^[A-ZÇĞİÖŞÜ][a-zçğıöşü]+$")

# Turkish orthography attaches grammatical suffixes to proper nouns with
# an apostrophe (Eda'yı, Eda'ya, Serkan'ın) -- stripped before counting
# so inflected mentions of the same name merge into one candidate
# instead of each inflection diluting its own count below threshold.
_SUFFIX_SPLIT = re.compile(r"['’].*$")

# Punctuation that can cling to a token after naive whitespace-splitting
# (sentence-final periods, commas, quote marks) -- stripped from both
# ends before pattern-matching, since e.g. "Eda." must still match.
_STRIP_CHARS = ".,!?;:\"'’()[]{}—–"

# Sentence-initial common words get capitalized by Turkish orthography
# exactly like real names do, so capitalization alone isn't enough
# signal. Two categories deliberately included:
#  - pronouns/interjections/acknowledgements (the obvious noise)
#  - kinship/honorific address terms (Anne, Baba, Abla, Abi, Teyze,
#    Amca, Hoca, Doktor, Bey, Hanım) -- in Turkish dialogue these are
#    used AS a form of direct address ("Anne, gel!") and so recur across
#    every episode of any Turkish-language series, not just this one;
#    without this they would pass both thresholds trivially and become
#    permanent false candidates.
STOPWORDS_TR = frozenset({
    "Ben", "Sen", "O", "Biz", "Siz", "Onlar", "Bu", "Şu",
    "Tamam", "Evet", "Hayır", "Peki", "Hadi", "Haydi", "Bak", "Dur",
    "Şey", "Neden", "Nasıl", "Ne", "Kim", "Nerede", "Ama", "Fakat",
    "Yani", "Şimdi", "Sonra", "Önce", "Belki", "Aslında", "Tabii",
    "Lütfen", "Pardon", "Hey", "Vay",
    "Anne", "Baba", "Abla", "Abi", "Teyze", "Amca", "Dayı", "Hala",
    "Hoca", "Doktor", "Bey", "Hanım", "Efendim",
    # Added 2026-09-20 from a real Season 01 (Love Is In The Air,
    # tvdb-383383) full-corpus mining run: these ordinary Turkish
    # function words/interjections were crowding real recurring
    # character names (Pırıl, Aydan, Ayfer, Erdem, Deniz, ...) out of
    # TOP_N_CANDIDATES. They pass the corroborated-mid-cue check (which
    # exists precisely to reject sentence-initial-only capitalization,
    # see test_sentence_initial_only_capitalization_not_counted_as_proper_noun)
    # not because they're genuine proper nouns, but because faster-
    # whisper's Turkish casing is imperfect and sometimes capitalizes
    # them mid-cue too -- a real, observed ASR noise floor, not a gap in
    # the position-based heuristic itself.
    "Bir", "Çok", "Yok", "İyi", "Öyle", "Aa", "Her", "Çünkü", "Ay",
    "Allah", "Bana", "Gerçekten", "Senin", "Bence", "Böyle", "Sana",
    "Gel", "Hiç", "Hem", "Niye", "Vallahi", "Güzel", "Seni", "Benim",
    "Ya", "Ee", "Beni", "Zaten", "Neyse", "Daha", "Ve", "Biraz", "Olur",
    "Bunu", "Teşekkürler", "Teşekkür", "Ha", "Eğer", "İşte", "En",
    "Bizim",
})

# A cue where every word is capitalized is never ordinary dialogue -- real
# example (Season 01, mostly S01E01-E05): faster-whisper transcribes the
# show's sung opening theme in Title Case ("Yanlışlarımdan Ders Alacak
# Kadar Olgun Değilim..."), unlike its normal sentence-case dialogue
# output. Every word in a cue like that matches the proper-noun pattern and
# most aren't cue-initial, so lyrics were mass-corroborating ordinary
# words as "proper nouns" and drowning out real character names. 4+ words
# keeps this from misfiring on a short, genuinely all-capitalized
# two/three-word dialogue line (rare, but "İyi Akşamlar" style greetings
# exist) that happens to have no lowercase word to contrast against.
_TITLE_CASE_MIN_WORDS = 4


def _is_latin_title_case_cue(text: str) -> bool:
    words = [t for t in (raw.strip(_STRIP_CHARS) for raw in text.split()) if t]
    return len(words) >= _TITLE_CASE_MIN_WORDS and all(w[:1].isupper() for w in words)


def _latin_extract_tokens(pattern: re.Pattern, stopwords: frozenset[str], *,
                          strip_suffix: bool) -> Callable[[str], Iterable[tuple[str, bool]]]:
    """Builds a Latin-capitalization extractor for a `pattern`/`stopwords`
    pair -- shared by every Latin-script language (tr, ms) so the actual
    scanning logic (whitespace-split, strip clinging punctuation, position
    for the corroboration check) is written once. `strip_suffix` applies
    Turkish's apostrophe-suffix stripping (Eda'yı -> Eda); languages
    without that orthographic convention (Malay) pass False."""
    def extract(text: str) -> Iterable[tuple[str, bool]]:
        for i, raw in enumerate(text.split()):
            token = raw.strip(_STRIP_CHARS)
            if strip_suffix:
                token = _SUFFIX_SPLIT.sub("", token)
            if pattern.match(token) and token not in stopwords:
                yield token, i == 0
    return extract


# ---------------------------------------------------------------------------
# Malay (ms): Latin capitalization, same mechanism as Turkish.
#
# Standard Malay orthography capitalizes proper nouns exactly like
# English/Turkish does, over the same plain Latin A-Z alphabet (no
# Turkish-style diacritic letters). UNVALIDATED against real Malay
# dialogue (checked 2026-09-29: zero real .ms.srt transcripts exist
# anywhere in this deployment's library yet) -- STOPWORDS_MS below is a
# starter list built from established Malay grammar (pronouns,
# question words, common interjections, kinship/honorific address
# terms -- the same categories STOPWORDS_TR covers), not yet refined
# against a real mining run's false positives the way STOPWORDS_TR's
# 2026-09-20 addition was. Expect to extend this the first time a real
# Malay series is mined and produces noisy candidates, same as Turkish's
# own history.
# ---------------------------------------------------------------------------

_LATIN_PROPER_NOUN_MS = re.compile(r"^[A-Z][a-z]+$")

STOPWORDS_MS = frozenset({
    "Saya", "Aku", "Kau", "Kamu", "Awak", "Dia", "Kami", "Kita", "Mereka",
    "Ini", "Itu",
    "Ya", "Tidak", "Tak", "Baik", "Jangan", "Boleh", "Tolong",
    "Apa", "Siapa", "Kenapa", "Mengapa", "Bagaimana", "Bila", "Mana",
    "Sudah", "Dah", "Belum", "Nanti", "Sekarang", "Tadi",
    "Kalau", "Jika", "Tapi", "Tetapi", "Jadi", "Memang", "Betul",
    "Wah", "Aduh", "Aduhai", "Alamak", "Eh", "Hei", "Oh", "Ya lah",
    "Mak", "Emak", "Ibu", "Ayah", "Bapa", "Kakak", "Kak", "Abang", "Bang",
    "Adik", "Dik", "Datuk", "Nenek", "Pakcik", "Makcik",
    "Encik", "Puan", "Cik", "Tuan", "Doktor", "Cikgu",
})


# ---------------------------------------------------------------------------
# Japanese (ja): katakana script-switching, not capitalization.
#
# Japanese prose is hiragana/kanji by default; a run of katakana
# characters is a genuine, distinct-script signal used for foreign
# names, loanwords, and stylized native names -- there's no sentence-
# initial-capitalization confound the way Latin scripts have, so every
# occurrence self-corroborates (is_initial is always reported False).
# ---------------------------------------------------------------------------

# Full-width katakana block (U+30A1-U+30FA) plus the prolonged sound
# mark U+30FC ("ー") -- 2+ characters, since a single kana is too
# ambiguous (common particles/onomatopoeia fragments use katakana too).
_KATAKANA_RUN = re.compile(r"[ァ-ヺー]{2,}")

# Some Japanese caption/AD-style sources prefix a cue with the speaking
# character's name in half-width katakana parentheses, e.g. "(ｶﾅｺ)ｳｰﾝ..."
# -- confirmed real content (2026-09-29 production audit: genuinely
# spoken audio, not an ASR artifact -- see CLAUDE.md), but it's a
# caption/attribution convention, not dialogue text, and stripping it
# avoids mining the same handful of speaker labels as if they were
# newly-recurring dialogue words.
_BRACKET_SPEAKER_PREFIX = re.compile(r"^[（(][ｦ-ﾝァ-ヺー]+[)）]")

# Measured 2026-09-29 against the only real .ja.srt transcripts in this
# deployment ("Happy Kanako's Killer Life" S02E01-03, "Hammer Session!"
# S01E01): common katakana loanwords/interjections that recur often
# enough to otherwise qualify but are ordinary vocabulary, not names --
# same empirical-refinement pattern as STOPWORDS_TR's 2026-09-20 entry.
STOPWORDS_JA = frozenset({
    "マジ", "バカ", "カッコ", "ダメ", "ターゲット", "サイレンサー", "バイク",
    "オッケー", "ホント", "クビ", "マフィア", "パチンコ", "サイト", "ブラック",
    "プライベート", "フリー", "カンパ", "インチキ", "スポンサー", "テスト",
    "パワハラ", "コミュ", "シャッター", "メガ", "スマホ", "カジノ", "ヤバ",
    "サンキュー",
})


def _katakana_extract_tokens(text: str) -> Iterable[tuple[str, bool]]:
    text = _BRACKET_SPEAKER_PREFIX.sub("", text)
    for m in _KATAKANA_RUN.finditer(text):
        token = m.group()
        if token in STOPWORDS_JA:
            continue
        yield token, False  # no position confound -- every hit self-corroborates


@dataclass(frozen=True)
class LanguageMiner:
    """One language's proper-noun mining strategy.

    `extract_tokens(cue_text)` yields (token, is_cue_initial) for every
    mining candidate in a cue -- `is_cue_initial` marks an occurrence
    that CANNOT alone corroborate the token as a genuine recurring
    proper noun, because it's confounded with ordinary sentence-initial
    capitalization (Latin scripts); a script with no such confound
    (Japanese) reports False unconditionally, so every occurrence
    corroborates immediately. `is_noise_cue(cue_text)` flags a whole cue
    to skip outright (e.g. an all-Title-Case sung lyric line); defaults
    to never skipping."""
    extract_tokens: Callable[[str], Iterable[tuple[str, bool]]]
    is_noise_cue: Callable[[str], bool] = staticmethod(lambda text: False)


MINERS: dict[str, LanguageMiner] = {
    "tr": LanguageMiner(
        extract_tokens=_latin_extract_tokens(_LATIN_PROPER_NOUN_TR, STOPWORDS_TR, strip_suffix=True),
        is_noise_cue=_is_latin_title_case_cue),
    "ms": LanguageMiner(
        extract_tokens=_latin_extract_tokens(_LATIN_PROPER_NOUN_MS, STOPWORDS_MS, strip_suffix=False),
        is_noise_cue=_is_latin_title_case_cue),
    "ja": LanguageMiner(extract_tokens=_katakana_extract_tokens),
}

# A single episode's ASR mishear can recur a few times within THAT
# episode (a decoder fixation on one bad segment) without being a real
# name -- 3 within one ~40-45 min episode is enough separation from that
# noise floor to count as "used repeatedly" rather than "glitched".
# Reused unchanged across every registered language: validated
# specifically for Japanese too (2026-09-29) against the same real
# transcripts STOPWORDS_JA was measured from -- both confirmed real
# names (スマイル, カズ) clear it from a single episode's occurrences,
# with no evidence yet that a different value is needed per language.
MIN_OCCURRENCES_PER_EPISODE = 3

# Deliberately 1, not higher (changed 2026-09-19, real request after a
# real miss: "Evren" -- a genuine recurring character whose name is also
# an ordinary Turkish word -- went unprotected long enough to visibly
# corrupt translation ("Mr. Universe") before a human noticed). Waiting
# for several episodes to "corroborate" a name trades real, current
# translation quality for a precision margin that promote() (see
# api.py's POST .../glossary/promote) now covers instead: a mined name
# is never auto-applied to translation on its own -- surfacing sooner
# and requiring an explicit one-click human promotion is the actual
# safety boundary now, not an episode count.
MIN_DISTINCT_EPISODES = 1

# Caps the hotwords string from growing unbounded across many seasons of
# a long-running series; ranked by total_count descending before the cap
# is applied, so the most confidently recurring names always win a slot.
TOP_N_CANDIDATES = 30


@dataclass
class AutoCandidate:
    canonical: str
    episode_counts: dict[str, int] = field(default_factory=dict)
    total_count: int = 0


def mine_series_entities(series_root: str | Path, source_lang: str = SOURCE_LANG, *,
                         exclude_srt_path: str | Path | None = None,
                         exclude_canonicals: set[str] | None = None) -> list[AutoCandidate]:
    """Scans every `*.{source_lang}.srt` under series_root (recursive --
    seasons are subdirectories) that has a matching `*.en.srt` sibling
    (paired existence is the "this episode's pipeline run actually
    completed" signal -- an episode mid-processing or that failed before
    writing translation output is not a trustworthy mining source), and
    returns names that recur often enough, within and across episodes,
    to be trusted (see MIN_OCCURRENCES_PER_EPISODE/MIN_DISTINCT_EPISODES).

    Degrades to [] for any `source_lang` not registered in MINERS (no
    orthographic mining strategy exists for it yet -- see this module's
    docstring), same as its existing "no match of any kind" behavior; a
    caller doesn't need to check membership itself first.

    exclude_srt_path (this job's own current/prior source output -- so a
    job never mines its own not-yet-validated, or on retry previous-bad,
    output) and exclude_canonicals (casefolded; already-manually-
    protected names) are both applied DURING counting, not after
    ranking, so an excluded name never occupies one of the
    TOP_N_CANDIDATES slots a genuinely new name could use.

    A per-file read/parse error skips just that one file -- one corrupt
    sibling must not blank out signal from every other good episode. No
    match of any kind returns []."""
    miner = MINERS.get(source_lang)
    if miner is None:
        return []
    root = Path(series_root)
    exclude_path = Path(exclude_srt_path).resolve() if exclude_srt_path else None
    exclude_canonicals = exclude_canonicals or set()

    candidates: dict[str, AutoCandidate] = {}
    # A function word capitalized only because it opened a cue/sentence
    # never earns a slot here -- see LanguageMiner's docstring. Global
    # across the whole series (not per-episode): one genuine mid-cue
    # sighting anywhere is enough to corroborate the name everywhere.
    corroborated: set[str] = set()
    for tr_path in sorted(root.rglob(f"*.{source_lang}.srt")):
        if exclude_path is not None and tr_path.resolve() == exclude_path:
            continue
        en_path = tr_path.with_name(tr_path.name[: -len(f".{source_lang}.srt")] + ".en.srt")
        if not en_path.exists():
            continue
        try:
            cues = srt.parse(tr_path)
        except (OSError, ValueError, UnicodeDecodeError):
            continue

        episode_key = str(tr_path)
        per_episode: dict[str, int] = {}
        for cue in cues:
            if miner.is_noise_cue(cue.text):
                continue
            for token, is_initial in miner.extract_tokens(cue.text):
                if token.casefold() in exclude_canonicals:
                    continue
                per_episode[token] = per_episode.get(token, 0) + 1
                if not is_initial:
                    corroborated.add(token)

        for token, count in per_episode.items():
            if count < MIN_OCCURRENCES_PER_EPISODE:
                continue
            entry = candidates.setdefault(token, AutoCandidate(canonical=token))
            entry.episode_counts[episode_key] = count
            entry.total_count += count

    qualified = [c for c in candidates.values()
                if len(c.episode_counts) >= MIN_DISTINCT_EPISODES and c.canonical in corroborated]
    qualified.sort(key=lambda c: c.total_count, reverse=True)
    return qualified[:TOP_N_CANDIDATES]


def detect_series_mining_language(series_root: str | Path) -> str | None:
    """Which registered MINERS language this series' own already-
    completed episodes are actually in, found by looking at what
    `*.{lang}.srt` siblings exist under series_root -- not guessed from
    the current job (see mine_series_entities_auto()'s docstring for why
    that's the wrong question to ask here). Returns None if no
    registered language has any matching file (nothing mined yet, or
    the series is in a language MINERS doesn't cover)."""
    root = Path(series_root)
    for lang in MINERS:
        if next(root.rglob(f"*.{lang}.srt"), None) is not None:
            return lang
    return None


def mine_series_entities_auto(series_root: str | Path, *,
                              exclude_srt_path: str | Path | None = None,
                              exclude_canonicals: set[str] | None = None) -> list[AutoCandidate]:
    """mine_series_entities(), but for a caller that doesn't know (or
    shouldn't assume) the series' source language up front -- real gap
    fixed 2026-09-29: worker.py's own suggestion-refresh used to call
    mine_series_entities() with the hardcoded module-level SOURCE_LANG
    ("tr") for every job regardless of the series' actual language, so
    non-Turkish series always mined zero candidates silently. Mining
    targets *other, already-resolved* episodes of the same series (see
    this module's docstring) -- their language is a property of the
    series, discoverable from what's already on disk
    (detect_series_mining_language()), not of the job invoking this,
    which may not know its own resolved language yet (AUTO source-
    language mode isn't resolved until ASR runs)."""
    lang = detect_series_mining_language(series_root)
    if lang is None:
        return []
    return mine_series_entities(series_root, lang, exclude_srt_path=exclude_srt_path,
                                exclude_canonicals=exclude_canonicals)


def write_suggestions(suggestions_dir: str | Path, tvdb_id: int,
                      candidates: list[AutoCandidate], *, title: str | None = None) -> Path:
    """Writes <suggestions_dir>/<tvdb_id>.yaml, schema-identical to a
    real /glossary series file (tvdb_id/title/entities[canonical,
    aliases, protected]) plus reviewer-only extra keys (occurrences,
    distinct_episodes) -- load_profile() already ignores every key on a
    non-`protected: true` entity, so this file is safe even if ever
    pointed at by mistake as a real glossary source. `protected: false`
    by default: promoting a name to translation protection is a human
    decision, made by copying it into the real /glossary YAML.

    Full regenerate-and-overwrite every call -- this module recomputes
    per job, statelessly; see module docstring."""
    directory = Path(suggestions_dir)
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "tvdb_id": tvdb_id,
        "title": title,
        "entities": [
            {
                "canonical": c.canonical,
                "aliases": [],
                "protected": False,
                "occurrences": c.total_count,
                "distinct_episodes": len(c.episode_counts),
            }
            for c in candidates
        ],
    }
    target = directory / f"{tvdb_id}.yaml"
    glossary_files.write_text_atomic(
        target, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    return target
