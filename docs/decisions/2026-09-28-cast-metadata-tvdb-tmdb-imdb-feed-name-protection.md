# Cast metadata: TVDB + TMDB + IMDb feed name protection (since 2026-09-28)

`cast_metadata.py` pulls per-episode character credits from TMDB
(`TMDB_API_KEY`), TheTVDB (`tvdb_client.episode_characters()`,
`TVDB_API_KEY`), IMDb's free non-commercial datasets (no key -- the
official IMDb API is paid and scraping imdb.com is against its terms) and
the series' `tvshow.nfo`, merged on the diacritic-folded first name
(services disagree on surnames). `cast_enrichment.py` then applies the
evidence bar below automatically: a credited name is protected only if the
series' own source subtitles use it *as a name* and the production engine
demonstrably loses it unprotected. What passes is written as a
`source: metadata`, `case_sensitive: true`, episode-scoped entry with its
evidence and committed to the glossary repo; entries a person wrote are
never edited (the report only flags them). The worker re-checks each
series every `SUBTITLE_AI_CAST_REFRESH_DAYS` (12 hours) while idle;
`scripts/refresh_cast.py` runs it on demand. First run (Love Is In The
Air): 8 names protected, each with real mistranslations behind it (Kiraz ->
"Cherry" 30/30 lines, Balca -> "The hammer", Melek -> "The angel", Sevda ->
"Love"), while names that translate fine unprotected (Ayfer, Semiha, ...)
were left alone.

Two glossary features exist because of this and apply to any entry:
`episodes: ["S01E29-E40", ...]` (the job's SxxEyy decides; an unknown
episode gets no scoped names) and `case_sensitive: true`. Deniz is the
reference case: "sea" in S01E01-E28, a character from E29 -- see the
comment on its entry in `love-is-in-the-air.yaml`.

Known limit: a capitalised common word inside a scoped name's episodes is
still protected (e.g. the pun "O yanındaki Melek değil, şeytan", angel vs
the character Melek). The evidence gate keeps such names few; don't widen
`name_shaped()` to count sentence-initial capitals as names.

**Known limit: the evidence gate only works for Latin-script source
languages** (confirmed 2026-09-28, "Hammer Session!" {tvdb-177461}, a
Japanese drama). `cast_enrichment.name_shaped()` searches for the
candidate's ROMANIZED name (from TMDB/TVDB/IMDb, e.g. "Tachibana") as a
literal substring of the source subtitle TEXT -- for a Japanese `.ja.srt`
(kanji/kana), that string can never appear, so every candidate reports
`used as a name in 0 lines / 0 episodes` and nothing is ever protected,
regardless of how real or well-credited the cast is. Confirmed the DATA
side works fine (`scripts/refresh_cast.py`, no `--dry-run`, pulled 25 real
named cast members from TMDB/IMDb/TVmaze/the local `.nfo` correctly) --
this is specifically the evidence-matching step, not an import failure.
Not fixed this session (would need matching against the actual script the
candidate's real name is written in, which TMDB/TVDB don't reliably
supply in kanji) -- flagged here so a future session doesn't waste time
re-diagnosing "why does cast metadata never protect anything for this
series" from scratch.
