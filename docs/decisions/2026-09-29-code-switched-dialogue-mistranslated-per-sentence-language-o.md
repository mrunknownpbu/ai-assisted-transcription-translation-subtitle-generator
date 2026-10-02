# Code-switched dialogue mistranslated: per-sentence language override (2026-09-29)

**Real user report:** "If You Love" (2023) S01E01's cold-open scene (a
short exchange in Spanish before the main Turkish story starts) was
"not picked up and translated." Investigation found something more
specific: faster-whisper (forced to `language="tr"` for the whole job,
per its own single detected-language design) still transcribed the
Spanish dialogue CORRECTLY as real Spanish text at 04:18-04:32
(`Si, si.` / `Este hombre es increíble.` / `Mire el estado de esta
casa.` / `Todas las noches es así.` / `No es posible.`) -- Whisper's
forced-language conditioning doesn't perfectly constrain output token
choice on audio that strongly doesn't match it. The bug was downstream:
`translate.translate_spans()` translated the WHOLE job with one fixed
`src_lang` (Turkish), so NLLB decoded the Spanish text as if it were
Turkish, producing a mix of lucky near-misses (`No es posible.` ->
"It's not possible."), garbled hybrids (`Todas las noches es así.` ->
"Todas las noches is like that."), and one line left completely
untranslated in the English output (`Mire el estado de esta casa.`).
Checked the rest of the season's `.tr.srt` files: isolated to this one
scene, but a real architectural gap -- any code-switched scene in any
episode would hit the same failure.

**Why not a naive per-sentence langdetect override:** measured first
(scripts run against real cached `.tr.srt` files, not synthetic
examples) before writing any detection code, because a naive approach
is actively dangerous here. Across 90,076 real Turkish subtitle cues (43
episodes, the whole `turkish/` library), `langdetect` misidentifies over
10% of short/normal-length Turkish cues as some OTHER language at
p>=0.90 confidence -- real examples: "Vay be!" -> English p=0.9999,
"Evet, evet." -> Danish p=0.9999, "Lan sen kime vuruyor musun lan?" (31
chars) -> Finnish p=1.0. A single sentence's high-confidence detection
is not trustworthy evidence on its own at typical subtitle-cue lengths.

**Fix (`subtitle_ai/langid.py`, new module; `translate.py`):**
`detect_language_overrides()` only overrides a RUN of 3+ CONSECUTIVE
sentences (each >=20 chars) that all independently agree on the same
non-source language at confidence >=0.90 -- a single misfire, or the
same wrong answer repeated on an identical/near-identical line (the
other two real false-positive shapes found in the library scan: "Berit,
Berit nerede?" said twice -> German p=0.999 both times; a slang phrase
repeated once -> Finnish p=1.0 both times), never forms a qualifying
run, because langdetect's mistake repeats identically while a genuine
scene keeps producing different sentences that still agree. Validated
against the WHOLE library: zero false positives across all 90,076 cues,
while correctly catching both real code-switch instances present in it
(this Spanish scene, and a run of English song lyrics quoted in Love Is
In The Air S01E20). `translate_spans()` groups sentences by detected
language and translates each group through its own NLLB tokenizer
(`_translate_flat_sentences()`); the model itself is language-agnostic
(only the tokenizer differs per source language, confirmed by
`load_model()`'s own docstring), and under model residency (this
deployment's default) an override group reuses the SAME already-loaded
model, only adding a cached tokenizer -- never a second GPU load. A
sentence overridden to the TARGET language (English -- the real shape
of the song-lyric case) is passed through verbatim, never sent to NLLB,
since round-tripping already-correct English through a translation
model risks paraphrasing it. Toggle: `SUBTITLE_AI_CODE_SWITCH_DETECTION`
(default on; `off`/`0`/`false`/`no` restores the old single-`src_lang`
behavior), matching `SUBTITLE_AI_ORPHAN_CONTEXT_PADDING`'s pattern.
`srt_translation.py`'s existing `_detect_source_language()` (Workflow
B's whole-file `source_lang="auto"` detection) now delegates its core
langdetect call to the same new `langid.py` module instead of
duplicating it. New tests: `tests/test_langid.py` (12, including all
three real measured false-positive shapes as regressions) and
`tests/test_translate.py::CodeSwitchDetectionTests`/
`CodeSwitchDetectionEnabledTests` (7). Full suite: 1185 passed, 40
subtests.

**Second production verification pass found a real recall gap, closed
with clause-level detection.** Running a real `POST /api/jobs` against
this exact episode (deploy + fresh ASR decode, not a cache hit) revealed
the sentence-level fix didn't fire: this decode punctuated the same
scene as only 2 comma-joined sentences (`Este hombre es increible, mide
el estado de esta casa.` / `Todas las noches es asi, no es posible.`)
instead of the original run's 4-5 period-separated ones, so
sentence-level detection never accumulated the 3 independent signals
`min_run` requires -- the fix correctly, conservatively declined to
override rather than acting on 2 signals, since the earlier measurement
had already shown `min_run=2` is unsafe (see below). Before changing
anything, re-measured: lowering `min_run` to 2 (with a same-language
re-check on the concatenated run text, and separately with a
distinct-non-identical-text requirement) was tested against the whole
library and produced **14 real false positives against only 2 true
positives** -- genuine Turkish dialogue (dash-formatted exchanges,
repeated names like "Serkan Bolat") misidentified as Dutch/Indonesian/
German/Italian/Danish/Catalan at confidence 1.0, and concatenating the
run's text to give the detector more context did NOT help discriminate
(both the true and false cases read back at p=1.000 either way) --
confirming these aren't a "not enough text" problem but a genuine
character-pattern confusion that more of the same wrong text only
reinforces. `min_run=3` was correct and was not lowered.

Instead, `detect_language_overrides()` now runs detection on `texts`
split further on commas (`langid._clauses()`) -- signal granularity
only, never translation granularity: a flat sentence with 2+ comma
clauses is still translated as ONE NLLB call, but each of its clauses
counts independently toward the run. Re-validated against the whole
90,076-cue library at the identical thresholds: **zero new false
positives** (most of the real Turkish false-positive shapes -- short
dash-dialogue, repeated proper nouns -- split into fragments too short
to individually qualify, rather than producing new spurious agreement)
and the real comma-merged scene now correctly forms a run of 3
qualifying clauses from its 2 sentences. Verified end to end against
the real episode a third time (redeploy + `POST /api/jobs`, `.tr.srt`
unchanged from the ASR cache hit): `Este hombre es increible, mide el
estado de esta casa.` -> "This man is incredible, measuring the state
of this house."; `Todas las noches es asi, no es posible.` -> "It's
like this every night, it's not possible." -- both now coherent,
correct English. The one remaining imperfect line, `Ateş, Ateş, si,
si.` -> "Fire, fire, si, si.", is a single sentence genuinely mixing
Turkish and Spanish within one clause-free unit; no clause of it
individually reached the confidence/length gate, so it correctly stays
untouched rather than guessing -- a disclosed limitation, not a bug.
`needs_review` (28) and the rest of the 3,683-cue episode's translation
were unchanged from the pre-fix baseline (spot-checked, no new
misroutes). Full suite: 1186 passed, 40 subtests.
