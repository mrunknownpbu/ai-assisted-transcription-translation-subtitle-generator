# CLAUDE.md

Guidance for a future Claude Code session working in this repo. For
project architecture/workflows, see `README.md` first -- this file is
deploy/ops/precedent knowledge that isn't written down anywhere else.


## Where the rest went

- `docs/operations.md` -- ops behavior: workdir cleanup, translate-server, GPU preflight, job lifecycle.
- `docs/decisions/` -- the dated decision log (measurements, shipped/rejected experiments, incident
  write-ups). Start at `docs/decisions/README.md`; a reference like `CLAUDE.md ("<title>")` in code or
  docs means the entry with that title there. Newest entries are last in the index.
- Add a new dated entry as a file in `docs/decisions/` plus a row in its README, not here. Keep this
  file to standing rules and commands.

## Tests

```bash
cd /opt/projects/subtitle-ai && PYTHONPATH=subtitle_ai uv run --with pytest pytest tests -q
```

Plain `python -m pytest` / `pytest` also work if your environment
already has the right interpreter on `PATH` and deps installed (see
`.github/workflows/test.yml` for the exact CPU-only CI setup) -- the
`uv run` form above is the one that reliably works from a fresh shell.

Baseline as of 2026-09-28: 1065 passing, 0 failures (plus 89 frontend
tests -- `cd frontend && npm test -- --run`; up from 939 after the
natural-dialogue plan's five steps -- see the segmentation-naturalness,
turn-detection and ASR-style entries elsewhere in this file). Every
backend test is `unittest`-style, so with no pytest available this also
works:
`cd subtitle_ai && PYTHONPATH=.:../tests ../.venv/bin/python -m unittest discover -s ../tests -t ../tests`.
CI installs an explicit package list (`.github/workflows/test.yml`), not
uv.lock -- a new runtime dependency must be added there too (ruamel.yaml
was, 2026-09-28; numpy was, 2026-09-28, for turns.py -- see pyproject.toml).
Verified directly on the
current host (myphy-ai), not just in CI or inside Docker: 0 skips once
`ffmpeg`, `uv`, and `node`/`npm` are on the bare host too, not just inside
the image -- CI's own setup installs `ffmpeg` as a separate step for the
same reason (see `.github/workflows/test.yml`). (History: 708 after
fixing `test_glossary_profile.py`'s stale `"Eda Yıldız"` canonical
assertion 2026-09-22 -- see `docs/IMPROVEMENT_PLAN.md` section 1.1 -- then 727
after the lightweight-sampler-model tests (2.1), 737 after VRAM
pre-flight (2.2), 757 after orphan-context-padding (3.2), 756 after
removing `tvdb_client.characters()`'s dead-code test (3.3), 768 after the
worker stage/progress tests (4.3), 795 after the batch-queueing and
SRT-editor tests (4.1/4.2), then 830 (2026-09-25) before the run that took
it to 876: WebVTT-as-`.srt` source support, the Series page's folder-name
title fallback and episodes-vs-jobs count fix, `.vtt` uploads, the Jobs
page Refresh-button fix, and `SUBTITLE_AI_COMPUTE_TYPE`; then 939 after
docs/ENHANCEMENT_DRAFT.md's round (2026-09-28). Update this line rather than
leaving it to drift the next time the count moves.)

Frontend: `cd frontend && npx tsc --noEmit && npm test -- --run`.

## Deploying

```bash
./scripts/deploy.sh                   # local only (the default since 2026-09-27)
./scripts/deploy.sh <remote-host>     # also redeploy a remote translate-server, if one exists
```

Builds `subtitle-ai:dev` and redeploys the local `subtitle-ai` container.
The remote leg (ship the same image to `<remote-host>`, redeploy
`translate-server` there from `compose.translate-server.yml`, poll its
health) only runs if you pass a host. It used to default to
`media-server` -- exactly backwards for a fresh checkout on a new host
(see "Host" below): it would have silently shipped an image to, and
redeployed, an old deployment's remote server that this checkout has
nothing to do with. `SKIP_REMOTE=1` still forces the remote leg off even
if you do pass a host.

App health: `curl http://localhost:8099/api/health` -- reports
`worker_last_heartbeat_seconds_ago` when the worker thread is alive; a
large/growing value means the thread is wedged, not just busy (the
heartbeat updates on every pipeline-stage event, not just once per poll,
so a long-running job doesn't itself look like a stall).

Remote translate-server health (only relevant if one is actually
deployed -- see "Host" below): `curl http://<remote-host>:8091/health`.

## Host

subtitle-ai runs on **myphy-ai** (`10.1.1.110`, LAN; `10.1.20.110`,
storage/NFS subnet; SSH alias `myphy-ai`) since a 2026-09-27 migration to
dedicated hardware -- a Ryzen 5 5500 + RTX 3070, nothing else sharing the
GPU. No remote translate-server is currently deployed anywhere
(media-server's was decommissioned the same day as part of the
migration); the code path still exists and works (`TRANSLATE_SERVER_URL`,
`translate_server.py`), it's just unset today. The prior host (a Tesla
P4 shared with Tdarr transcode workloads) was fully purged that day --
container, image, appdata (models/glossary/cache/backups), Docker build
cache, and the repo checkout itself -- so don't expect to find anything
subtitle-ai-related there again without redoing the migration.

`git`/`gh` are set up on myphy-ai so a session can commit/push and use
`gh` directly from there: origin is the HTTPS remote, and
`gh auth setup-git` supplies push credentials from the existing `gh`
login -- no separate SSH deploy key was needed. Running the test suite
directly on the bare host (not just inside Docker, where they're already
present) needs `uv`, `ffmpeg`, and `node`/`npm` -- all three were missing
on the fresh Ubuntu install and were installed as part of this migration.
If a host is ever set up from scratch again, don't forget them: the
Docker image having a dependency says nothing about the bare host having
it, and `uv run --with pytest`/`npm test` above need the bare host's own
copies.

### Host setup checklist

Everything here lives on the host, not in the image or the repo, so a
new host needs each one redone by hand. The 2026-09-27 migration had to
rediscover most of them one failure at a time:

1. `uv`, `ffmpeg`, `node`/`npm` on the bare host (tests; see above).
2. `gh auth login` + `gh auth setup-git` (push over HTTPS).
3. `.env` from `.env.example` (`DATA_PATH`, `CONFIG_PATH`, and
   `SUBTITLE_AI_COMPUTE_TYPE` to match the GPU; optionally the TMDB/TVDB,
   Sonarr/Radarr and Plex/Jellyfin keys).
4. Daily backup cron (`crontab -e`):
   `0 3 * * * /opt/projects/subtitle-ai/scripts/backup_jobs_db.sh >> /opt/docker/appdata/subtitle-ai/backups/backup.log 2>&1`
   -- then run the script once by hand and check a `.db.gz` appears.
   (It needs no `sqlite3` CLI: it falls back to python3's backup API.)
5. Copy `${CONFIG_PATH}/subtitle-ai/{models,glossary,cache}` across;
   the glossary directory is its own git repo -- copy `.git` too.

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
  and commit there too; this repo's git history won't show it. Web-UI
  edits (promote/update/delete) commit themselves since 2026-09-28
  (`glossary_files.py`, author "subtitle-ai web UI"), and are written
  with ruamel.yaml round-trip so comments survive. Before that, a UI
  promotion's `yaml.safe_dump()` silently deleted every evidence comment
  in `love-is-in-the-air.yaml` (restored in that repo's history). The
  comments ARE the evidence record for the "evidence bar" section below
  -- never write these files with plain pyyaml.
- **Job database** (`${CONFIG_PATH}/subtitle-ai/cache/jobs.db`,
  SQLite/WAL): backed up daily at 03:00 by a host crontab entry running
  `scripts/backup_jobs_db.sh` (safe against the live DB; 14-day
  retention in `${CONFIG_PATH}/subtitle-ai/backups/`). The crontab is
  per-host state -- it silently did NOT survive the 2026-09-27 migration
  and was reinstalled 2026-09-28; see "Host setup checklist" above.

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

The same discipline applies in the other direction: any rule emitted at
>=0.7 feeds `needs_review` on every job. Until 2026-09-28 readability's
min-duration rule sat at exactly 0.7 and was 99.97% of all
`needs_review` hits in production (62,490 of 62,512), burying the ~21
real entity/hallucination findings; it and max-duration now emit at
`readability_qc.DURATION_CONFIDENCE` (0.5) -- timing isn't fixable in the
text-only editor, and Workflow B inherits it from the source SRT anyway.
Stored rows were recounted with `scripts/recompute_needs_review.py`
(20,058 -> 22). Before raising any rule to >=0.7, count how often it
fires across the real job database first.

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
translation protection directly -- promoting a MINED name to `protected:
true` is always a deliberate human/session decision. The one automatic
path is `cast_enrichment.py` (see `docs/decisions/2026-09-28-cast-metadata-tvdb-tmdb-imdb-feed-name-protection.md`), which enforces this same bar
in code -- credited by cast metadata, recurring as a name, and a measured
mistranslation -- and scopes what it protects to credited episodes.
