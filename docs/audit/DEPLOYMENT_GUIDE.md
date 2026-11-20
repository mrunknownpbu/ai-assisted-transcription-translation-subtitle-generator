# Deployment guide (this host, 2026-11-18)

What was actually deployed and how to operate it. The repository's own `docs/deployment.md` remains the general reference.

## State

| Item | Value |
|---|---|
| Host | Ubuntu 26.04, Ryzen 5 5600G, 30 GB RAM, RTX 3070 8 GB, driver 580.178.04, Docker 29.8.2, Compose v5.5.1 |
| Source | `/opt/project/repo`, branch `audit/2026-11-18`, base commit `57f9d48` **plus uncommitted changes** (F-01 `media.py`, F-10 `useEventStream.ts`, tests, `docs/audit/`). The image was built from this working tree, so it is not reproducible from git until the changes are committed. |
| Image | `subtitle-ai:dev` = `subtitle-ai:2026-11-18-audit1`, 10.4 GB |
| Container | `subtitle-ai`, restart policy `unless-stopped`, Docker enabled at boot, runs as uid/gid 1000 |
| URL | `http://<host>:8099/` (published on all interfaces, plain HTTP) |
| API key | in `/opt/project/repo/.env` (`SUBTITLE_AI_API_KEY`, file mode 600, git-ignored). A copy of `.env` is in the backups folder, mode 600. |
| Media | `DATA_PATH=/data/media` mounted at `/data` in the container. Deliberately **not** the whole NAS share, so the container cannot see `/data/scripts`, downloads or the recycle folder. |
| GPU | `NVIDIA_VISIBLE_DEVICES=all`, `SUBTITLE_AI_COMPUTE_TYPE=float16`; every other setting is the repository default (dedicated-GPU profile) |
| Remote translate server | not deployed |
| Integrations | none configured (no Sonarr, Radarr, TVDB, TMDB, Plex, Jellyfin or webhook) |

Persistent data, all under `/opt/docker/appdata/subtitle-ai/`: `models/` (12 GB, mounted read-only),
`cache/` (job DB, scratch, GPU lock, uploads), `glossary/`, `backups/`.
`/opt/docker/appdata/subtitle-ai-testlib/` is a scratch library with a 3-minute Turkish clip used for the tests; it is
not mounted now and can be deleted.

Model weights present: `Systran/faster-whisper-large-v3`, `faster-whisper-small`, `facebook/nllb-200-distilled-1.3B`
(Hugging Face format plus a CTranslate2 float16 conversion in `models/ct2/`), `Helsinki-NLP/opus-mt-tc-big-tr-en`.

## Day-to-day commands

```bash
cd /opt/project/repo
docker compose ps
docker compose logs -f subtitle-ai
curl -s localhost:8099/api/health
docker exec subtitle-ai nvidia-smi
docker exec subtitle-ai python -c 'import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))'
```

Check the queue is idle before recreating the container: an interrupted job is requeued automatically (up to 3 times)
but re-transcribes from the start.

## Redeploy and roll back

```bash
cd /opt/project/repo
docker tag subtitle-ai:dev subtitle-ai:previous          # keep the running image
./scripts/deploy.sh                                       # build, recreate, wait for health
# roll back
docker tag subtitle-ai:previous subtitle-ai:dev && docker compose up -d subtitle-ai
```

`subtitle-ai:2026-11-18-audit1` is the image deployed in this audit. The job database only gains columns, so an older
image can read a newer database.

## Exposure

The API requires `X-API-Key` on every route except `/api/health`. There is no TLS. Keep port 8099 on the LAN, or put a
reverse proxy with HTTPS in front that forwards `Host` and `Origin` unchanged. Do not publish it to the internet.
The key is stored in the browser's local storage after the UI prompts for it once.
