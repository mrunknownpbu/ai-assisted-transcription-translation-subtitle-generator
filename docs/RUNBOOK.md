# Production Runbook

## Starting / stopping

```bash
docker compose up -d                 # CPU profile
docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up -d   # NVIDIA profile
docker compose down                  # stop, keep volumes (media/output/db/models survive)
docker compose down -v               # stop AND wipe all data — destructive, confirm first
```

## Health checks

- `GET /api/health` → `{"status": "ok"}`. Also wired into the `api` container's Docker
  `HEALTHCHECK` (`docker compose ps` shows `healthy`/`unhealthy`).
- `GET /api/hardware` → confirms what accelerator (if any) the container actually sees.
  Compare against the host's real hardware if something looks wrong (e.g. `vendor: "cpu"`
  when you expected `nvidia` usually means the NVIDIA Container Toolkit isn't installed, or
  you started the CPU compose file instead of layering `docker-compose.nvidia.yml`).

## Logs

```bash
docker compose logs -f api           # API + in-process worker threads (same process)
docker compose logs -f frontend      # nginx access/error logs
```

Every job's own stage-by-stage log is also queryable without touching container logs:
`GET /api/jobs/{id}/logs` returns `stages` (per-stage status + provenance),
`suppressions` (every hallucination-defense decision with reason/method/threshold), and
`qc_reports` (every QC finding, pass and fail).

## Common operational tasks

**A job is stuck in `queued`**
1. `GET /api/hardware` — if `gpu_count > 0` but no job is running, check
   `docker compose logs api` for a GPU-slot timeout (30 min default,
   `JobRunner.GPU_SLOT_WAIT_TIMEOUT_S`) or an exception during a previous job that left a
   slot held. A held slot only releases on the holding job's completion/exception — a
   killed container (not a graceful shutdown) can leave a stale `held_by` row in
   `gpu_slots`; restart the `api` container to re-run `init_gpu_slots` at startup, or clear
   the table manually (see "Manual DB fixes" below).
2. Confirm `WORKER_THREADS >= 1` in the environment.

**A job failed**
- `GET /api/jobs/{id}` → `error_message` has the exception. `GET /api/jobs/{id}/logs` has
  the full stage history including which ASR/translation engines were attempted and why
  each one failed or was skipped.
- Retry via `POST /api/jobs/{id}/retry` (only valid from `failed` — resets `retry_count` to
  0 and re-queues). Jobs also auto-retry up to `max_retries` (default 2) before landing in
  `failed`.

**Re-processing media that already has output**
- Submitting a new job for a `media_file_id` that already has completed `Output` rows lands
  the new job in `needs_decision` instead of `queued` — it will not run until
  `POST /api/jobs/{id}/existing-output-decision` is called with `{"decision": "keep"}`
  (cancels the new job, existing output stays) or `{"decision": "replace"}` (queues the new
  job normally). The UI's Job Queue page surfaces this as Keep/Replace buttons.

**Canceling / pausing / reordering**
- `POST /api/jobs/{id}/cancel` — valid from `queued`, `running`, `paused`, or
  `needs_decision`.
- `POST /api/jobs/{id}/pause` / `.../resume` — only valid on a `queued` job (holds it out of
  the claim queue without canceling).
- `PATCH /api/jobs/{id}/priority` with `{"priority": N}` — higher runs first; only valid on
  a `queued` job. The queue claims by `(priority DESC, created_at ASC)`.

## Backups

The only thing that needs backing up beyond the media/output files themselves is
`db_data` (`subtitles.db`, SQLite in WAL mode):

```bash
docker compose exec api sqlite3 /data/db/subtitles.db ".backup /data/db/backup.db"
docker cp $(docker compose ps -q api):/data/db/backup.db ./subtitles-backup-$(date +%F).db
```

Using `.backup` (not a raw file copy) is important under WAL mode — a plain `cp` of
`subtitles.db` while the WAL file has uncheckpointed writes can produce an inconsistent
snapshot.

## Manual DB fixes (last resort)

Everything is reachable via a normal SQLite client if the API can't express what you need:

```bash
docker compose exec api python3 -c "
from app.db.session import session_scope
from app.db.models import GpuSlot
with session_scope() as s:
    s.query(GpuSlot).update({'held_by': None, 'acquired_at': None})
    s.commit()
"
```

This forcibly frees every GPU slot — only do this when you've confirmed no job is actually
using the GPU right now (check `docker compose logs api` for an in-flight ASR/translation
call first).

## Resource exhaustion

- The `api` container has hard CPU/memory limits (`API_CPU_LIMIT`/`API_MEM_LIMIT` in
  `.env`) so a runaway job can't take down the host. If jobs are being OOM-killed
  (`docker compose logs api` shows the process just stops mid-stage, and
  `docker inspect <container> --format '{{.State.OOMKilled}}'` reports `true`), lower
  `WHISPER_MODEL_SIZE` / the NLLB model size, or raise `API_MEM_LIMIT` if the host has
  headroom.
- `SUBTITLE_MAX_CONCURRENT_GPU_JOBS` (in `app/config.py`, default 1 per detected GPU) is
  the primary VRAM-safety knob — raising it lets more jobs share a GPU concurrently at the
  cost of higher per-job memory pressure and slower wall-clock time per job.

## Upgrading

```bash
git pull
docker compose build           # rebuild images with the same torch index as before
docker compose up -d           # recreates containers, volumes (and therefore the DB/models/output) untouched
```

The SQLite schema is created via `Base.metadata.create_all()` at startup (see
`db/session.py`). **This only creates tables that don't exist yet — it does NOT add new
columns to an existing table.** A brand-new table introduced by an upgrade appears
automatically; a new column added to an *existing* table (e.g. `jobs`) does not, and the
API will fail on it with `sqlite3.OperationalError: table X has no column named Y` the
first time it tries to write that column. There is no Alembic-style migration tooling yet
(a known gap, noted here rather than glossed over) — two ways to recover:

- **No data worth keeping** (a dev/test instance): stop the stack and drop just the DB
  volume, leaving media/output/models intact:
  ```bash
  docker compose down
  docker volume rm <project>_db_data   # see `docker volume ls` for the exact name
  docker compose up -d                 # recreates the DB from scratch on next startup
  ```
- **Real data to preserve**: back up the DB first (see Backups above), then add the missing
  column(s) by hand before restarting, e.g.:
  ```bash
  docker compose exec api sqlite3 /data/db/subtitles.db \
    "ALTER TABLE jobs ADD COLUMN job_type TEXT DEFAULT 'audio_pipeline';"
  ```
  Check `app/db/models.py`'s diff against your running version for the exact columns/types
  a given upgrade added.
