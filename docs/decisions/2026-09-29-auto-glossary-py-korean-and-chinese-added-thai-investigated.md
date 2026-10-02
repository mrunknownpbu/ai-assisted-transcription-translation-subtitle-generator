# auto_glossary.py: Korean and Chinese added, Thai investigated and deferred (2026-09-29)

Follow-up to the entry above. The user pointed out that although this
deployment has no `*.ko.srt`/`*.zh.srt` sidecar files yet (nothing has
been transcribed through this pipeline for those languages), many
Korean and Chinese library files carry embedded original-language
subtitle streams inside the `.mkv` containers themselves (`ffprobe`
confirmed 5,375 Korean-tagged and 5,878 Chinese-tagged embedded subtitle
streams across the two libraries). Extracting a few real episodes from
two unrelated series per language gave real dialogue text to measure
against, closing the "no real data" gap that had left `ko`/`zh` out of
the previous round.

**Korean (`ko`):** word-spaced (unlike ja/zh/th), but Hangul has no
capitalization-equivalent marker. Measured (5 real episodes of
"Confidence Queen" tvdb-444735 + 3 of "Not Others" tvdb-428265): raw
particle-stripped frequency mining over one series' top 40 candidates
was only ~5-8% genuine names -- far noisier than Japanese's katakana
signal. What discriminates well: a candidate that ALSO recurs (>=3
occurrences) in a second, completely unrelated series is real, measured
proof it's ordinary vocabulary, not a name specific to one show's cast.
`STOPWORDS_KO` is exactly that measured 249-word cross-series overlap
(plus a hand-curated kinship/honorific supplement, same category as
`STOPWORDS_TR`'s Anne/Baba/Bey entries) -- after filtering, genuine
names (제임스/James, 전태수, 재희, 조성우, 레이첼/Rachel) rose to roughly
15-20% of what remained. Disclosed as noisier than tr/ms/ja: thematic
common nouns specific to one show's plot (a title, a profession) have
no marker distinguishing them from a name and aren't caught by any
stopword list -- still safe per this module's standing hotwords-only/
human-reviewed-suggestions framing.

**Chinese (`zh`):** no word spacing AND no capitalization-equivalent
marker -- harder than `ko`. Candidates are 2-4 character n-grams over a
sliding window (no real word boundaries to split on), which on its own
produces redundant overlapping fragments of the same real term (a real
3-character sword name "先元剑" also makes its own 2-character
sub-strings "先元"/"元剑" independently clear the frequency thresholds).
New `_collapse_substring_redundant_zh_candidates()` drops a shorter
candidate when a longer candidate containing it explains ~all of its
occurrences, keeping only the longer, more informative term. The same
cross-series-overlap technique validated for `ko` (measured: "Pull
Strings" tvdb-467966 + "A Familiar Stranger" tvdb-425354, 127-word
overlap seeding `STOPWORDS_ZH`) discriminates well here too -- what's
left is dominated by real, thematically relevant recurring terms for
one show (a character name 长庚, a place 熊岛/Bear Island, sect/
organization names 西昉教 and 度仙门, the artifact name 先元剑) -- exactly
the class of entity a series glossary exists to protect, the スマイル/
"Smile" pattern from the entry above.

**Thai (`th`) investigated, NOT implemented.** Real embedded Thai
subtitle streams do exist too (confirmed, same method) and were
extracted from two unrelated series ("Thicha" tvdb-457128, "Only You"
tvdb-454376) -- but measuring the same character-n-gram technique
against real Thai text showed it doesn't transfer: Thai's Unicode block
mixes base consonants with combining vowel/tone marks that have no
standalone meaning (unlike Chinese, where every Han character IS a
meaningful unit on its own), so a sliding codepoint window produces
almost entirely meaningless fragments (e.g. a vowel+tone-mark pair with
no consonant at all) rather than real syllables or words. Real Thai
word segmentation needs either a proper segmentation library (a new
dependency, e.g. `pythainlp`) or a different lightweight heuristic not
yet found -- shipping the same n-gram technique here would produce a
suggestions list that's overwhelmingly noise, worse than not offering
suggestions at all. Left unimplemented rather than forced; revisit if a
real segmentation approach is worth adding as a dependency.

New tests: `tests/test_auto_glossary.py` gained `KoreanMiningTests` (5)
and `ChineseMiningTests` (5), including a real regression case proving
the substring-collapse keeps `先元剑` whole while still keeping `先元`
when it ALSO recurs independently outside the longer term.
`UnregisteredLanguageTests` now uses `th` (the actual remaining gap)
instead of `ko`/`zh`. Full suite: 1211 passed, 40 subtests.

Verified with real (not synthetic) dialogue text: `mine_series_entities()`
run directly against the actual extracted embedded-subtitle-stream
content from both Korean series and both Chinese series (copied into a
temp series-root matching the `*.{lang}.srt` + `*.en.srt` pairing gate),
confirmed `detect_series_mining_language()` correctly identifies `ko`/
`zh` from what's on disk and produces the exact candidates described
above. A full live `POST /api/jobs` run wasn't done for these two (no
Korean/Chinese episode has been transcribed through this pipeline's own
ASR yet, so there's no real job to point at the way "Happy Kanako" S02
already had); `worker.py`'s wiring itself is covered by the existing
`AutoHotwordsTests` suite, which passed unchanged.
