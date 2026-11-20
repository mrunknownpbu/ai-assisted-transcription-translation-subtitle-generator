# Backup and recovery

## What matters

| Data | Where | Back up? |
|---|---|---|
| Job database (SQLite, WAL) | `/opt/docker/appdata/subtitle-ai/cache/jobs.db` | yes, daily |
| Series glossaries | `/opt/docker/appdata/subtitle-ai/glossary/` | yes (small; edited from the UI) |
| Configuration and API key | `/opt/project/repo/.env` | yes, mode 600, keep out of git |
| Model weights | `/opt/docker/appdata/subtitle-ai/models/` (12 GB) | optional, re-downloadable |
| Generated subtitles | next to the videos on the NAS | covered by the NAS |
| Transcript cache, scratch, uploads | `cache/` | no |

## Backup (verified)

```bash
/opt/project/repo/scripts/backup_jobs_db.sh        # online backup, gzip, 14-day retention, safe while running
cp -a /opt/docker/appdata/subtitle-ai/glossary /opt/docker/appdata/subtitle-ai/backups/glossary-$(date +%F)
```

Run on 2026-11-18: wrote `backups/jobs-20261118-143212.db.gz` (8 KB). It was restored to a temporary file, `PRAGMA
integrity_check` returned `ok` and all 10 job rows were present. The live database reports `journal_mode=wal`.

A daily cron line is **not installed**. To install it:

```bash
( crontab -l 2>/dev/null; echo '0 3 * * * /opt/project/repo/scripts/backup_jobs_db.sh >> /opt/docker/appdata/subtitle-ai/backups/backup.log 2>&1' ) | crontab -
```

## Restore

```bash
cd /opt/project/repo
docker compose stop subtitle-ai
cp /opt/docker/appdata/subtitle-ai/cache/jobs.db /opt/docker/appdata/subtitle-ai/cache/jobs.db.broken   # keep what is there
rm -f /opt/docker/appdata/subtitle-ai/cache/jobs.db-wal /opt/docker/appdata/subtitle-ai/cache/jobs.db-shm
gunzip -c /opt/docker/appdata/subtitle-ai/backups/jobs-YYYYMMDD-HHMMSS.db.gz > /opt/docker/appdata/subtitle-ai/cache/jobs.db
python3 -c "import sqlite3;print(sqlite3.connect('/opt/docker/appdata/subtitle-ai/cache/jobs.db').execute('pragma integrity_check').fetchone())"
docker compose up -d subtitle-ai && curl -s localhost:8099/api/health
```

## Recovery behaviour (tested)

Restarting the container while a job ran kept all 10 job records, requeued the interrupted job (`recovery_count` 1 of
3, reason recorded) and it completed after the restart.

## Rollback

Image: see `DEPLOYMENT_GUIDE.md`. Configuration: restore `.env` from `backups/env-2026-11-18.bak`. Nothing in the
media library is modified by a deploy or a rollback.
