# "If You Love" (2023): lead character's name translated as "Fire" -- stale cast-enrichment evidence, not a code bug (2026-09-30)

**Real user report:** rewatching S01E01's cold open, the lead character's
name ("Ateş" -- also the ordinary Turkish word for "fire") was rendered
as "Mr. Fire" ("Ateş Bey"), "fire archer" ("...Ateş Arcalı"), and "Fire,
the Arc of Fire." ("Ateş, Ateş Arcalı.") throughout, instead of being
preserved as a name.

**Root cause:** not a bug in `cast_enrichment.py`'s protection logic --
it had genuinely run the day before (`checked_at: 2026-09-29`) and
correctly declined to protect anything, because only 1 of the 6 episodes
had a transcript on disk at that point and the evidence gate requires
`name_episodes >= 2` (`MIN_NAME_LINES = 5`, see `cast_enrichment.py`).
The real gap: nothing re-triggers the check when more episodes finish
transcribing later the same day -- `worker._maybe_refresh_cast()` only
retries a series once `cast_enrichment.is_stale()` clears, which is a
flat 30-day interval (`refresh_days()`), regardless of new episode data
becoming available. So the correct "not enough evidence yet" skip from
day 1 silently became a stale, now-wrong "nothing protected" state once
episodes 2-6 existed, with no automatic path to re-check for a month.

**Fix applied (config data, not code):** manually re-ran
`cast_enrichment.enrich_series(435293, ...)` against all 6 now-available
episodes. It protected 10 real credited characters with strong
per-name evidence (`unprotected_probe`: how many of up to 30 sampled
bare-name lines lost the name when translated unprotected) --
`Ateş` 30/30, `Leyla` 30/30, `Meryem` 30/30 (rendered "Mary"), `Yakup`
23/30 ("Jacob"), `Onur` 11/11, `Barış` 24/24, `İlter` 9/30, `Umut` 27/30,
`Füsun` 5/30, `Bige` 3/14 -- written to the new
`glossary/tvdb-435293.yaml`, all `case_sensitive: true` (same tradeoff
as `Deniz` in `love-is-in-the-air.yaml`: avoids the common-noun reading
being wrongly protected, at the cost of not catching a lowercase ASR
mis-transcription of the name).

**Verified against real regenerated output**, not just the tool's own
report: backed up all 6 episodes' `.tr.srt`/`.en.srt`, re-ran
`POST /api/jobs` with `overwrite_original`/`overwrite_english` for all
of them (real ASR-cache-hit + fresh translation pass), confirmed "Ateş"
now survives translation throughout every episode (e.g. E03 "Mr. Ateş,
I think Mrs. Berit...", E05 "Mr. Ateş, would you like some coffee?").

**A narrower, disclosed residual gap remains** (not fixed, not a
regression from this fix): case-sensitive exact-string protection
doesn't cover the name with a Turkish case suffix glued on --
`Ateşi` (accusative, "...preferred **Ateş** to me" -- E02) and `Ateşin`
(genitive, "**Ateş's** [presence]..." -- E06) still translate as "Fire"
since neither matches the protected string `Ateş` exactly.
`auto_glossary.py`'s Turkish *miner* only strips apostrophe-prefixed
suffixes (`_SUFFIX_SPLIT`), so it does not solve these unseparated forms;
runtime matching had the same gap. Two other same-scene "Fire" hits
checked and are NOT this bug: E04's is a correct translation of a
genuine common-noun usage (an Olympic-torch joke, "ateşle" lowercase),
and E06's is an unrelated ASR truncation artifact (source text was cut
to "Ate.", not the protection matching failing).

**Still unaddressed, separate issue:** the Spanish cold-open dialogue
in S01E01 ("Senor, si, kien es?" / "A kien es es buscando?") remains
mistranslated -- this is the already-documented, disclosed code-switch
detection gap (clauses too short to reach the 3-clause corroboration
threshold, see "Code-switched dialogue mistranslated" above), not a new
finding.

**Open question, not yet acted on:** should `worker._maybe_refresh_cast()`
re-check a series sooner than 30 days when its episode count grows after
a "not enough evidence" skip, instead of only on error? Would close the
staleness gap this bug came from generally, not just for this one
series -- not implemented, needs the same measure-first validation as
everything else in this file before changing the scheduling policy.
