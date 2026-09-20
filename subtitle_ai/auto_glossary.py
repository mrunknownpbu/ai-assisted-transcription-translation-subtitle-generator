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

v1 is Turkish-source-only, matching every other glossary/hotwords
feature in this codebase today -- SOURCE_LANG is hardcoded rather than
threaded from the current job's own source_lang, which sidesteps AUTO
mode's chicken-and-egg problem: this job's real language isn't known
until its own ASR runs, but mining targets *other, already-resolved*
episodes of the same series, whose language is a property of the
series, not of this job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

import srt

SOURCE_LANG = "tr"

# Matches faster-whisper's own Turkish capitalization behavior for
# proper nouns: a leading uppercase letter (ASCII or Turkish-specific),
# rest lowercase. ALL-CAPS is excluded on purpose -- Whisper occasionally
# emits emphasis/acronym-shaped artifacts in all-caps, which is never a
# genuine name.
PROPER_NOUN_PATTERN = re.compile(r"^[A-ZÇĞİÖŞÜ][a-zçğıöşü]+$")

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
STOPWORDS = frozenset({
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

# A single episode's ASR mishear can recur a few times within THAT
# episode (a decoder fixation on one bad segment) without being a real
# name -- 3 within one ~40-45 min episode is enough separation from that
# noise floor to count as "used repeatedly" rather than "glitched".
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

# A cue where every word is capitalized is never ordinary dialogue -- real
# example (Season 01, mostly S01E01-E05): faster-whisper transcribes the
# show's sung opening theme in Title Case ("Yanlışlarımdan Ders Alacak
# Kadar Olgun Değilim..."), unlike its normal sentence-case dialogue
# output. Every word in a cue like that matches PROPER_NOUN_PATTERN and
# most aren't cue-initial, so lyrics were mass-corroborating ordinary
# words as "proper nouns" and drowning out real character names. 4+ words
# keeps this from misfiring on a short, genuinely all-capitalized
# two/three-word dialogue line (rare, but "İyi Akşamlar" style greetings
# exist) that happens to have no lowercase word to contrast against.
_TITLE_CASE_MIN_WORDS = 4


def _is_title_case_cue(text: str) -> bool:
    words = [t for t in (raw.strip(_STRIP_CHARS) for raw in text.split()) if t]
    return len(words) >= _TITLE_CASE_MIN_WORDS and all(w[:1].isupper() for w in words)


@dataclass
class AutoCandidate:
    canonical: str
    episode_counts: dict[str, int] = field(default_factory=dict)
    total_count: int = 0


def _candidate_tokens(text: str):
    """Yields (token, is_cue_initial) -- position within the cue matters
    because Turkish orthography capitalizes the first word of a sentence
    exactly like it capitalizes a real name. A pure function word
    ("Bir", "Ya", "Çok", "İyi"...) is capitalized ONLY by virtue of
    starting a cue/sentence and reverts to lowercase everywhere else; a
    genuine proper noun stays capitalized wherever it appears. See
    mine_series_entities' `corroborated` set, which uses this to reject
    the former without a hand-maintained function-word list covering
    all of Turkish."""
    for i, raw in enumerate(text.split()):
        token = raw.strip(_STRIP_CHARS)
        token = _SUFFIX_SPLIT.sub("", token)
        if PROPER_NOUN_PATTERN.match(token) and token not in STOPWORDS:
            yield token, i == 0


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

    exclude_srt_path (this job's own current/prior source output -- so a
    job never mines its own not-yet-validated, or on retry previous-bad,
    output) and exclude_canonicals (casefolded; already-manually-
    protected names) are both applied DURING counting, not after
    ranking, so an excluded name never occupies one of the
    TOP_N_CANDIDATES slots a genuinely new name could use.

    A per-file read/parse error skips just that one file -- one corrupt
    sibling must not blank out signal from every other good episode. No
    match of any kind returns []."""
    root = Path(series_root)
    exclude_path = Path(exclude_srt_path).resolve() if exclude_srt_path else None
    exclude_canonicals = exclude_canonicals or set()

    candidates: dict[str, AutoCandidate] = {}
    # A function word capitalized only because it opened a cue/sentence
    # never earns a slot here -- see _candidate_tokens' docstring. Global
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
            if _is_title_case_cue(cue.text):
                continue
            for token, is_initial in _candidate_tokens(cue.text):
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
    tmp = target.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(target)
    return target
