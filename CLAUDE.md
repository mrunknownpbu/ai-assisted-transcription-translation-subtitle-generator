# CLAUDE.md

Guidance for a future Claude Code session working in this repo. For
project architecture/workflows, see `README.md` first -- this file is
deploy/ops/precedent knowledge that isn't written down anywhere else.

## Tests

```bash
cd /opt/projects/subtitle-ai && PYTHONPATH=subtitle_ai uv run --with pytest pytest tests -q
```

Plain `python -m pytest` / `pytest` also work if your environment
already has the right interpreter on `PATH` and deps installed (see
`.github/workflows/test.yml` for the exact CPU-only CI setup) -- the
`uv run` form above is the one that reliably works from a fresh shell.

Baseline as of 2026-09-21: 632 passing, 1 pre-existing failure
(`test_glossary_profile.py::RealGlossaryDataTests::test_loads_real_series_profile`
-- a real-glossary-data assertion mismatch on `"Eda Yıldız"` vs the
production glossary's current `"Eda"` canonical, unrelated to whatever
you're working on unless you're touching that specific entity).

Frontend: `cd frontend && npx tsc --noEmit && npm test -- --run`.

## Deploying

```bash
./scripts/deploy.sh [remote-host]     # default remote-host: media-server
```

Builds `subtitle-ai:dev`, redeploys the local `subtitle-ai` (master)
container, ships the same image to the remote GPU host, redeploys
`translate-server` there from `compose.translate-server.yml`, and polls
both health endpoints. Set `SKIP_REMOTE=1` to skip the remote leg (e.g.
a change that only touches frontend/API code, not `translate.py`/
`translate_server.py`).

Master health: `curl http://localhost:8099/api/health` -- reports
`worker_last_heartbeat_seconds_ago` when the worker thread is alive; a
large/growing value means the thread is wedged, not just busy (the
heartbeat updates on every pipeline-stage event, not just once per poll,
so a long-running job doesn't itself look like a stall).

Remote translate-server health: `curl http://<remote-host>:8091/health`.

## Environment / secrets

Copy `.env.example` to `.env` and fill in `DATA_PATH`/`CONFIG_PATH`.
Everything else in it is optional with a working default. Don't confuse
this with a homelab-wide `.env` some deployments symlink into this
project directory for unrelated services -- if you see one, it's not
this project's config.

## Data durability

- **Glossary YAML** (`${CONFIG_PATH}/subtitle-ai/glossary/*.yaml`,
  mounted read-write at `/glossary`) is its OWN git repo, initialized in
  place directly on the host -- NOT part of this repository. If you edit
  a glossary file as part of a fix, `cd` into that directory on the host
  and commit there too; this repo's git history won't show it.
- **Job database** (`${CONFIG_PATH}/subtitle-ai-v2/cache/jobs.db`,
  SQLite/WAL): back up with `scripts/backup_jobs_db.sh` (safe to run
  against the live DB). No cron job is installed automatically -- see
  that script's header for the recommended line if you want scheduled
  backups.

## QC is advisory, not a gate -- on purpose

`pipeline.py`/`srt_translation.py`'s `valid` computation only fails a
job on segmentation/timing/output-structural QC findings.
Translation/entity/hallucination/readability findings never block
completion -- real, measured false-positive rates (harmless
interjection-collision "substitution" matches, embellishment-but-not-
wrong translation_error cases) were too high to safely auto-fail on.
Instead, `JobQc.needs_review_count()` (`qc/types.py`) surfaces only
high-confidence findings (entity_error/hallucination categories, or
anything >=0.7 confidence) as a `needs_review` count on the job record,
shown ahead of the generic QC summary in the job list GUI. If you're
tempted to make QC block completion, check the false-positive rate on
real data first -- it's higher than it looks from reading the heuristics
alone.

## Entity-protection precedent: the evidence bar

Before adding a name to a series glossary's `protected: true` list
(`glossary/<series>.yaml`), the established bar from real investigations
this project has done is: (1) confirm it's a genuinely recurring
character via corpus-wide occurrence counts across many episodes, not
just the one flagged instance, AND (2) find at least one concrete
example of it being mistranslated/hallucinated -- a common-word
collision ("Cenk" = "war", "Evren" = "universe", "Erdem" = "virtue",
"Deniz" = "sea"), a bare-exclaimed-name hallucination ("Sirius!" ->
"Sirius, what are you doing?"), or inconsistent transliteration. A name
that's merely recurring but shows no confirmed bug (e.g. "Ayfer" in the
Love Is In The Air investigation) is deliberately left unprotected --
protection isn't free of risk for common-word collisions, so don't add
it speculatively. A one-scene character (e.g. "Fatma", "Faruk") is
excluded regardless of translation quality, on recurrence alone.

`auto_glossary.py` mines a series' own already-completed episodes for
candidate names automatically, but only feeds ASR hotwords, never
translation protection directly -- promoting a mined name to `protected:
true` is always a deliberate human/session decision, never automatic.

## TheTVDB integration is metadata-only, unused for entities

`tvdb_client.py` exists and works (series title enrichment is wired into
`glossary_profile.load_profile()`), but `tvdb_client.characters()` --
fetching TheTVDB's cast list -- is dead code, called nowhere. No
`TVDB_API_KEY` is configured in production, so none of this currently
does anything. Don't assume TVDB is a source of entity/character data
for the glossary; `auto_glossary.py`'s corpus-mining is the only thing
that actually populates candidate names today, and it works from the
show's own real dialogue, not billing/cast metadata.

## Readability vs. content-completeness: an accepted, disclosed tradeoff

Fixing multi-sentence content-truncation (a source cue with 2+ real
sentences silently losing everything after the first) increased
readability-QC findings season-wide, because the fix means more cues,
which means less time-per-cue on average. This was judged worth it
(content correctness over cosmetic pacing) and is NOT something to
"fix" by reverting the truncation fix. A follow-up (two-speaker dash
dialogue kept as one cue for its whole envelope,
`segmentation_target.segment()`) recovered some of the readability
regression without sacrificing content. The remaining gap above the
pre-fix baseline is accepted, not a bug.

## Unpunctuated run-on source cues: partially shipped

A source cue with zero sentence-ending punctuation (can't be split by
`glossary.split_into_sentences()`) sometimes gets garbled by NLLB in one
`generate()` call -- real example, a source cue containing an aside
("oh, dear Selin") translated as "I'm Moon Flood." **Phase 1** (QC
visibility only -- flags this shape as a `translation_error` finding,
changes no translation behavior) is shipped. **Phase 2** (a bounded
chunk-and-compare retry, validated on real data: 227/1157 flagged cues
with a protected-entity signal genuinely improved, zero regressions in a
manual sample) was implemented and tested on a real S01E01 job, but is
currently sitting in a git stash (`git stash list`), not merged --
holding pending a decision on whether to commit it. See the scoping plan
this session produced (ask if you can't find it) for the full design and
rationale before reviving it.
