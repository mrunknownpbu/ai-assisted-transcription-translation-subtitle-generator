"""Correct character names Whisper mishears as a near-identical name.

Measured 2026-09-28 (scripts/eval_transcription.py, 7 Love Is In The Air
episodes vs the human Turkish subtitles): 15% of character-name mentions
were lost, mostly as a ONE-LETTER confusion with another name -- Aydan ->
"Aydın" x33, Selin -> "Selim" x11. Whisper hears the name; it just picks the
more common spelling. The episode's cast list says which one it is.

A word is corrected only when all of these hold (precision over recall --
a wrong "correction" is worse than a miss):
* it is capitalised (Whisper capitalises names; "aydın" the word is not);
* it behaves like a name, not an ordinary word capitalised at a sentence
  start: the episode capitalises it MORE often than the series writes it
  lowercase (series = this episode's transcript plus the series' other
  original-language subtitles on disk). Measured on 7 episodes: with no
  such check "Senin" ("your") became "Selin" 84 times (precision 70%);
  "never seen lowercase" also blocked the biggest real fix, "Aydın" ->
  "Aydan" (lowercase 3x in the series, capitalised up to 33x per episode),
  while "Çilek yiyemem" (strawberries), "Keyfi yerinde", "Ayda yılda bir"
  (lowercase 5-8x, capitalised once) are correctly left alone;
* it is not itself the name of anyone in the series (a real "Selim"
  elsewhere in the cast is never touched), nor in this episode's list;
* exactly one name that applies to THIS episode is one letter away
  (diacritic-insensitive, names of 4+ letters).
Suffixes and punctuation are kept ("Aydın'ın," -> "Aydan'ın,"). Each change
is recorded as a Correction on the Word, with Whisper's text kept in
`original_text`, like normalize.py's rules.

Names that apply to an episode come from its glossary (episode-scoped
protected names) and the series' cast-metadata report (cast_enrichment.py:
every credited character, its nicknames and surnames, with its episode
scope) -- read from disk, never fetched during a job.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from cast_metadata import fold
from transcript import Correction, CorrectionKind, Word

def enabled() -> bool:
    """SUBTITLE_AI_NAME_CORRECTION=off|0|false|no disables it (default on)."""
    import os
    return os.environ.get("SUBTITLE_AI_NAME_CORRECTION", "").strip().lower() not in {"off", "0", "false", "no"}


MIN_NAME_LENGTH = 4
CONFIDENCE = 0.9
_WORD = re.compile(r"^(?P<pre>[^\w]*)(?P<base>[^\W\d_]+)(?P<suffix>['’][^\W\d_]+)?(?P<post>[^\w]*)$")


def _one_edit_apart(a: str, b: str) -> bool:
    """Levenshtein distance exactly 1."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(a) == len(b):
            i += 1
        j += 1
    return edits + (len(b) - j) + (len(a) - i) == 1


def _name_tokens(names) -> set[str]:
    """Single-word names, plus each word of multi-word ones ("Serkan Bolat"
    -> Serkan, Bolat), keeping only capitalised words of 4+ letters."""
    out = set()
    for name in names:
        for token in re.split(r"\s+", str(name).strip()):
            if len(token) >= MIN_NAME_LENGTH and token[:1].isupper() and token.isalpha():
                out.add(token)
    return out


def episode_names(tvdb_id: int | None, episode: tuple[int, int] | None, glossary_dir: str | None,
                  report: dict | None) -> tuple[set[str], set[str]]:
    """(names that apply to this episode, every name known for the series)."""
    import glossary_profile
    here, everywhere = set(), set()
    if glossary_dir and tvdb_id is not None and Path(glossary_dir).is_dir():
        scoped = glossary_profile.load_profile(glossary_dir, tvdb_id=tvdb_id, episode=episode,
                                               enrich_from_tvdb=False)
        full = glossary_profile.load_profile(glossary_dir, tvdb_id=tvdb_id, all_episodes=True,
                                             enrich_from_tvdb=False)
        here |= _name_tokens(f for e in scoped.entities for f in e.surface_forms)
        everywhere |= _name_tokens(f for e in full.entities for f in e.surface_forms)
    for cand in (report or {}).get("candidates") or []:
        forms = [cand.get("name"), *(cand.get("nicknames") or []), *(cand.get("full_names") or [])]
        tokens = _name_tokens(f for f in forms if f)
        everywhere |= tokens
        ranges = glossary_profile.parse_episode_scope(cand.get("scope"))
        if glossary_profile.in_scope(ranges, episode):
            here |= tokens
    return here, everywhere | here


def series_vocabulary(series_root: Path | None, language: str, exclude: Path | None = None) -> Counter:
    """Folded lowercase word counts from the series' `<stem>.<language>.srt`
    files (skipping `exclude`). Cached per (series, language, exclude)."""
    if series_root is None or not language:
        return Counter()
    key = (str(series_root), language, str(exclude) if exclude else None)
    if key not in _series_vocab_cache:
        from srt import parse
        vocab: Counter = Counter()
        for path in Path(series_root).rglob(f"*.{language}.srt"):
            if exclude is not None and path == exclude:
                continue
            try:
                for cue in parse(path):
                    for token in re.findall(r"[^\W\d_]+", cue.text):
                        if token[:1].islower():
                            vocab[fold(token)] += 1
            except (OSError, ValueError):
                continue
        _series_vocab_cache[key] = vocab
    return _series_vocab_cache[key]


_series_vocab_cache: dict = {}


class Vocabulary:
    """How often each folded word is written lowercase (series + episode)
    and capitalised (this episode)."""

    def __init__(self, words: list[Word], series: Counter | None = None):
        self.lower: Counter = Counter(series or {})
        self.upper: Counter = Counter()
        for w in words:
            m = _WORD.match(w.text)
            if m:
                (self.lower if m["base"][:1].islower() else self.upper)[fold(m["base"])] += 1

    def is_ordinary_word(self, folded: str) -> bool:
        return self.lower[folded] > 0 and self.lower[folded] >= self.upper[folded]


def lowercase_vocabulary(words: list[Word], series: Counter | None = None) -> Vocabulary:
    return Vocabulary(words, series)


def correct_word(word: Word, names_here: set[str], known: set[str],
                 vocabulary: Vocabulary | None = None) -> Word:
    m = _WORD.match(word.text)
    if not m or not m["base"][:1].isupper() or len(m["base"]) < MIN_NAME_LENGTH:
        return word
    base = m["base"]
    folded = fold(base)
    if folded in {fold(n) for n in known} or (vocabulary is not None and vocabulary.is_ordinary_word(folded)):
        return word
    matches = [n for n in names_here if _one_edit_apart(folded, fold(n))]
    if len(matches) != 1:
        return word
    name = matches[0]
    word.text = f"{m['pre']}{name}{m['suffix'] or ''}{m['post']}"
    word.corrections.append(Correction(
        kind=CorrectionKind.NAME, rule_id="cast-name-one-edit",
        evidence=f"'{base}' is one letter from '{name}', a character name for this episode "
                 f"(cast metadata / glossary), and not itself a name in this series",
        confidence=CONFIDENCE))
    return word


def correct_words(words: list[Word], names_here: set[str], known: set[str],
                  vocabulary: Vocabulary | None = None) -> list[Word]:
    """Pass `vocabulary` (built from the WHOLE episode, plus series_vocabulary())
    when correcting one segment at a time; by default it is built from `words`."""
    if not names_here:
        return words
    vocab = lowercase_vocabulary(words) if vocabulary is None else vocabulary
    return [correct_word(w, names_here, known, vocab) for w in words]
