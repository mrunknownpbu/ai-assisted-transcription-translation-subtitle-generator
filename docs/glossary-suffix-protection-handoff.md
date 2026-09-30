## Resolved (2026-09-30)

`Entity.turkish_case_suffixes` now enables direct, vowel-harmonized Turkish
case matching only for explicitly opted-in entities. `Ateş` is the sole
enabled entry in `tvdb-435293.yaml`; generic names do not gain the matcher.
The full backend suite passes (1215 tests, 48 subtests), and a scan of all 49
locally available Turkish subtitles found exactly 14 newly protected `Ateş`
forms across four episodes, with no unrelated changed lines. See
`CLAUDE.md`'s "Turkish glued case-suffix protection" entry for the measured
suffix distribution and design rationale.

## Historical problem

Protected character names in `subtitle_ai/glossary.py` don't survive translation when a Turkish case suffix is glued directly onto the name with no apostrophe. Confirmed real examples from "If You Love" (2023) S01:

- Source (E02): `özür dilerim Ateşi bana tercih ettiğini` (Ateş + accusative suffix "-i")
  Translated: `...preferred Fire to me` — should preserve `Ateş`.
- Source (E06): `Ateşin hayatımıza getirdiği olumlu tek şey olabilir.` (Ateş + genitive suffix "-in")
  Translated: `Fire may be the only...` — should preserve `Ateş`.

`Ateş` **is** protected for this series (`glossary/tvdb-435293.yaml`, `case_sensitive: true`) and the bare form translates correctly everywhere else. Only the suffixed forms leak through.

## Root cause

`protect()` (`subtitle_ai/glossary.py:126`) matches names via `_bounded()` (`subtitle_ai/glossary.py:27`):

```python
_ASCII_WORD = "[A-Za-z0-9_]"

def _bounded(pattern: str) -> str:
    return rf"(?<!{_ASCII_WORD}){pattern}(?!{_ASCII_WORD})"
```

This requires a non-ASCII-word-character boundary on both sides. In `Ateşi`, the suffix `i` is itself an ASCII letter immediately following `Ateş` with no separator, so the right-side boundary fails and the name is correctly-by-design *not* matched — which is wrong for Turkish agglutinative case suffixes attached with no apostrophe.

There is related but non-identical prior art: `subtitle_ai/auto_glossary.py`'s `_SUFFIX_SPLIT = re.compile(r"['’].*$")` strips a suffix when MINING candidate names — but only when it's preceded by an apostrophe (formal orthography: `Ateş'i`). Real transcripts in this deployment often omit the apostrophe (both examples above have none), so `_SUFFIX_SPLIT` would not catch these cases either. Treat it as reference for the shape of the problem, not a ready-made fix.

## Constraints

- Turkish suffix vowel harmony means the suffix isn't one fixed string (`-i/-ı/-u/-ü`, `-e/-a`, `-in/-ın/-un/-ün`, `-de/-da`, `-den/-dan`, `-le/-la`, sometimes with a buffer consonant like `-y-`/`-n-`, etc.). Don't hardcode a small guessed list — measure which suffix shapes actually occur on real protected names in this repo's real `.tr.srt` corpus, or bound the change to only case-marking suffixes (never derivational ones), so ordinary unrelated words aren't accidentally swallowed.
- This repo's convention (see `CLAUDE.md`) is measure-first: validate any change against real transcript data before landing it, not synthetic examples alone.

## Suggested approach

1. Scan the real `.tr.srt` corpus for how often a name from an existing series glossary (`glossary/*.yaml`) is immediately followed by a lowercase ASCII letter with no separator, to size the real scope and enumerate real suffix shapes.
2. Adjust matching in `subtitle_ai/glossary.py` (`_bounded()` / `protect()` / `restore()`) so a protected name followed directly by one of the measured case-suffix shapes still matches and restores correctly, without introducing false matches inside unrelated longer words.
3. Add regression tests in `tests/test_glossary.py` using the two real sentences above plus anything else the corpus scan surfaces.
4. Document the fix and its evidence in `CLAUDE.md`, following the existing dated-entry style in that file.

## Acceptance criteria

- [x] Both real example sentences protect `Ateş` as a placeholder and restore
  it with the suffix intact, preventing NLLB from seeing the literal word
  "fire".
- [x] Full test suite passes with no regressions (1215 passed, 48 subtests).
- [x] No new false-positive matches introduced: the 49-file Turkish-corpus
  comparison changed only the 14 measured `Ateş` case forms.
- [x] `CLAUDE.md` includes the dated root-cause, fix, and verification record.

## Relevant files

- `subtitle_ai/glossary.py` — `_bounded()`, `protect()`, `restore()`
- `subtitle_ai/auto_glossary.py` — `_SUFFIX_SPLIT` (related prior art, mining-time only)
- `glossary/tvdb-435293.yaml` — the real series glossary where this surfaced
- `tests/test_glossary.py` — existing test structure to extend
