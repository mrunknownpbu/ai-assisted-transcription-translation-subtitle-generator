# Host-specific notes (moved verbatim from CLAUDE.md)

Everything below was `CLAUDE.md`'s "Deploying", "Host", "Environment / secrets" and "Data durability" sections until 2026-10-02. `docs/deployment.md` is the maintained deployment guide; this is the host record it builds on.

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
