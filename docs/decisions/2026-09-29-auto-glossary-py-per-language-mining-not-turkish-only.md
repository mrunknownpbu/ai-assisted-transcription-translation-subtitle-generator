# auto_glossary.py: per-language mining, not Turkish-only (2026-09-29)

**Real gap found auditing "Happy Kanako's Killer Life" (2025) S02's
translation quality:** a talent-agency name (`スマイル`/"Smile") was
mistranslated as the common word "smile" -- e.g. "私と一緒にスマイル辞めない？"
(~"won't you quit Smile with me?") -> "Can you smile with me?" -- and this
series had no glossary file at all. Checking why the auto-miner hadn't
already surfaced it as a suggestion found the real cause: `auto_glossary.py`
hardcoded `SOURCE_LANG = "tr"` and a Turkish-Latin-capitalization-only
proper-noun regex, so mining silently produced zero candidates for every
non-Turkish series, not just this one -- confirmed via the real
`/cache/glossary_suggestions/449837.yaml` (`entities: []` before the fix).

**Fix:** `auto_glossary.py` now has a `MINERS: dict[str, LanguageMiner]`
registry, one strategy per language, instead of one hardcoded pattern:
- `tr` (unchanged): Latin capitalization + corroboration (a candidate
  only counts once also seen capitalized NOT cue-initial, since
  sentence-initial capitalization is a confound identical scripts share).
- `ms` (new): same Latin-capitalization mechanism as Turkish, own
  `STOPWORDS_MS`. Explicitly disclosed as an unvalidated starter list
  (checked 2026-09-29: zero real `.ms.srt` transcripts exist anywhere in
  this deployment yet) -- built from established Malay grammar the same
  categories STOPWORDS_TR covers, not yet refined against a real mining
  run's false positives the way STOPWORDS_TR's own 2026-09-20 addition was.
- `ja` (new): katakana script-switching, not capitalization -- Japanese
  has no capitalization at all, but a run of katakana (vs. the default
  hiragana/kanji prose) is a real, distinct-script signal for foreign
  names, loanwords, and stylized native names. No position confound
  exists the way Latin scripts have, so every occurrence self-corroborates.
  Validated against the only real `.ja.srt` transcripts in this deployment
  (Happy Kanako S02E01-03 + Hammer Session! S01E01): correctly surfaced
  real recurring names (`カナコ`/Kanako, `カズ`/Kazu, `ユイ`/Yui, `ナナ`/Nana)
  and, confirming the motivating bug, `スマイル` itself (3 occurrences in
  E03 alone, clears the existing thresholds unmodified). A half-width-
  katakana speaker-label prefix some cues carry (`(ｶﾅｺ)...`, confirmed
  genuinely spoken audio, not an ASR artifact -- see the code-switch
  detection entry above) is stripped before mining so it never becomes a
  candidate itself. `STOPWORDS_JA` seeded from real measured noise in the
  same mining run (common katakana loanwords/interjections like マジ,
  ダメ, オッケー, スマホ that otherwise recur enough to qualify).
- `ko`/`zh`/`th` deliberately NOT implemented. Investigated first: Hangul/
  Hanzi/Thai script have no capitalization-equivalent marker at all, and
  this deployment has zero real source-language transcripts in any of
  the three to validate a frequency-based alternative against (the
  Korean/Chinese/Thai `.srt` files present in the library are pre-
  existing English fansubs, not this pipeline's own output). Presented
  this to the user before writing anything: shipping an unvalidated
  heuristic for languages with no real evidence behind it would repeat
  exactly the mistake `docs/turkish-language-support.md` documents
  choosing NOT to make for a "production-grade Turkish resource" with no
  real consumer. Decision: build the architecture + ja/ms now, add
  ko/zh/th once real transcribed episodes exist to measure against.

`worker.py`'s `_refresh_glossary_suggestions()` (called by every job,
both video and SRT-translation) used to always call `mine_series_entities()`
with the hardcoded module `SOURCE_LANG`, so it silently mined nothing for
any non-Turkish series regardless of this fix -- now calls new
`mine_series_entities_auto()`, which discovers which registered language
a series is actually in from what `*.{lang}.srt` siblings already exist
on disk (`detect_series_mining_language()`) rather than assuming Turkish
or needing to know the current job's own not-yet-resolved AUTO-mode
language.

New tests: `tests/test_auto_glossary.py` gained `JapaneseMiningTests` (6),
`MalayMiningTests` (4), `UnregisteredLanguageTests` (1), and
`AutoLanguageDetectionTests` (4); all 15 pre-existing Turkish tests pass
unchanged (same default `mine_series_entities(root)` call, same behavior).
Full suite: 1201 passed, 40 subtests.

Verified against real production data: redeployed, backed up the real
`/cache/glossary_suggestions/449837.yaml` (`entities: []`), then ran a real
`POST /api/jobs` for the actual "Happy Kanako's Killer Life" S02E01
episode (KEEP semantics, `outputs: []`, no subtitle files touched). The
suggestions file went from `entities: []` to 9 real candidates including
`スマイル` (occurrences: 3, distinct_episodes: 1) and the protagonist's
name `カナコ` (occurrences: 34, distinct_episodes: 3) -- confirming the fix
end to end, not just via direct function calls. Promoting `スマイル` in the
Series UI (a human decision, per this module's own safety framing) is the
remaining step to actually protect it in translation; not done here.
