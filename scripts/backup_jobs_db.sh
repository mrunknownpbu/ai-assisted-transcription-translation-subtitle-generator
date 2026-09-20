#!/bin/sh
# Backs up the subtitle-ai job store (SQLite, WAL mode) using sqlite3's
# own online .backup command, which is safe to run against a live
# database -- no need to stop the container or worker first.
#
# Real gap this fixes (production-readiness audit, 2026-09-21): the job
# store had no backup mechanism at all -- a single file on the host,
# no dump/rotation job.
#
# Usage: ./backup_jobs_db.sh [source_db] [backup_dir]
# Defaults match this project's real deployment path (see compose.yml's
# CONFIG_PATH/cache mount).
#
# Recommended cron line (not installed automatically -- add yourself):
#   0 3 * * * /opt/projects/subtitle-ai/scripts/backup_jobs_db.sh >> /var/log/subtitle-ai-backup.log 2>&1

set -eu

SRC_DB="${1:-/opt/docker/appdata/subtitle-ai-v2/cache/jobs.db}"
BACKUP_DIR="${2:-/opt/docker/appdata/subtitle-ai-v2/backups}"
RETAIN_DAYS=14

if [ ! -f "$SRC_DB" ]; then
    echo "backup_jobs_db.sh: source database not found: $SRC_DB" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
timestamp=$(date +%Y%m%d-%H%M%S)
dest="$BACKUP_DIR/jobs-$timestamp.db"

sqlite3 "$SRC_DB" ".backup '$dest'"
gzip "$dest"

echo "backup_jobs_db.sh: wrote $dest.gz"

# Prune backups older than RETAIN_DAYS -- keeps the backup directory
# from growing unbounded on a host with no other retention policy.
find "$BACKUP_DIR" -name 'jobs-*.db.gz' -mtime "+$RETAIN_DAYS" -delete
