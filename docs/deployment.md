# Deployment

How to build, run, secure and validate Subtitle AI. Host-specific history (the
2026-09-27 migration, cron, host setup checklist) is in `docs/host-notes.md`;
operational behaviour of the worker, scratch directories and the remote translate
server is in `docs/operations.md`.

## Topology

One container, `subtitle-ai`, from the image `subtitle-ai:dev`: uvicorn serving the
FastAPI app and the built React UI on port 8080 (published as **8099**), with the
background worker thread in the same process. An optional second deployment of the
same image runs `translate_server.py` on another GPU host (`compose.translate-server.yml`,
port 8091); none is deployed today.

## Image

| Concern | How |
|---|---|
| Build | Two stages: `node:22-slim` builds the frontend (`npm ci`, `npm run build`); `python:3.12-slim-bookworm` installs the locked dependencies (`uv sync --frozen`, CUDA 12.1 torch), a pinned cuDNN 8 for CTranslate2, and copies the app and the built UI. |
| ffmpeg | A static build copied from `mwader/static-ffmpeg:7.1.1`, not apt. |
| User | Non-root `subtitle` (uid/gid 1000). Only `/cache` and `/glossary` need to be writable by that uid. |
| Health | `HEALTHCHECK` curls `/api/health` every 30 s (503 when the database or worker is down). |
| Start | `uvicorn main:app --host 0.0.0.0 --port 8080`. |
| Size | About 10 GB (CUDA libraries). CI lints the Dockerfile and builds only the frontend stage. |
| Determinism | Dependencies are pinned in `uv.lock`; base images and the ffmpeg image are pinned by tag, not digest. |

## Volumes and configuration

| Mount | Purpose | Mode |
|---|---|---|
| `${DATA_PATH}:/data` | Media library; subtitles are written next to videos | read-write |
| `${CONFIG_PATH}/subtitle-ai/models:/models` | Model weights | read-only |
| `${CONFIG_PATH}/subtitle-ai/cache:/cache` | Job database, scratch work, transcript cache, GPU lock, cast cache, uploads | read-write |
| `${CONFIG_PATH}/subtitle-ai/glossary:/glossary` | Series glossaries (may be a Git repository) | read-write |

Configuration comes from `.env` (copy `.env.example`) through `compose.yml`.
Compose passes only variables it lists, so a new setting must be added to
`.env.example`, `compose.yml` and the README "Tuning knobs" table (a test checks).
GPU: `NVIDIA_VISIBLE_DEVICES=all` and a one-GPU device reservation.
`SUBTITLE_AI_COMPUTE_TYPE` must match the GPU generation.

## Security at deployment time

- **Bind behaviour.** The container listens on `0.0.0.0`. The server therefore
  refuses to start unless `SUBTITLE_AI_API_KEY` is set **or**
  `SUBTITLE_AI_ALLOW_INSECURE=1` acknowledges an open API on a trusted network.
  Set exactly one of them in `.env` before (re)starting.
- **Key.** With a key set, every route but `/api/health` requires `X-API-Key`; the
  UI prompts for it once and stores it in the browser's `localStorage`.
- **TLS.** None is terminated by the app. Put a reverse proxy in front for HTTPS and
  keep the `Host` and forwarded headers intact (the Origin check compares them).
- **Trust assumption.** A LAN where every client may be trusted with the media
  library, or a proxy that authenticates. Details and threats:
  `docs/architecture.md`, "Security model".
- **Secrets** (API keys for TMDB, TVDB, Plex, Jellyfin, Sonarr, Radarr, the webhook
  URL) are environment variables in `.env` (mode 600, git-ignored); they are not
  written to job records or logs.

## Build, deploy, roll back

```bash
./scripts/deploy.sh                  # docker build -t subtitle-ai:dev . ; docker compose up -d subtitle-ai ; wait for health
./scripts/deploy.sh <remote-host>    # also redeploy a remote translate-server
docker compose logs -f subtitle-ai
```

Recreating the container interrupts a running job; on start the store re-queues
interrupted jobs (up to 3 automatic recoveries) and a cached transcript makes the
re-run cheap. Check the queue is idle first (`curl localhost:8099/api/queue`).

Rollback: keep the previous image before building (`docker tag subtitle-ai:dev
subtitle-ai:previous`), and re-tag and `docker compose up -d` to go back. The job
database is migrated forward only and additively (new columns); an older image
reads a newer database but ignores the new columns.

Backups: `scripts/backup_jobs_db.sh` (daily cron; see `docs/host-notes.md`).

## Validation checklist (do not claim success from a successful build)

After building and recreating the container:

1. `docker ps` shows it `healthy`; `docker logs subtitle-ai` has no traceback, the
   startup guard did not refuse (or you set the key / opt-in), and lines carry a
   `[job=...]` tag once a job runs.
2. `curl -s localhost:8099/api/health` returns `"ok":true,"status":"ok","db_ok":true,"worker_alive":true`.
3. API: `curl -s localhost:8099/api/queue` (with `-H "X-API-Key: ..."` if keyed);
   without the key a keyed deployment answers 401.
4. Frontend: `curl -s localhost:8099/` serves the page and `/assets/*` loads; open it.
5. GPU: `docker exec subtitle-ai python -c "import torch; print(torch.cuda.is_available())"`
   prints `True`, and `nvidia-smi` shows memory used during a job.
6. A real **Workflow A** job on a short clip: completes, output `.srt` and
   `.<lang>.srt` exist and are valid, `config_snapshot` is present, logs show the
   stages with the job ID.
7. A real **Workflow B** job: completes without loading ASR, writes `.en.srt` and
   the original beside the video.
8. A batch of two, a cancellation of a queued job, a retry of a failed job.
9. An atomic-output failure: make the destination unwritable and confirm the job
   fails with `OUTPUT_ERROR` and the existing subtitle is unchanged.
10. Rescan: with Plex or Jellyfin configured, confirm `MEDIA_SERVERS_NOTIFIED` in the
    job log; otherwise record that it was not testable.

Record what was run and seen in `docs/handover.md`, including anything skipped.
