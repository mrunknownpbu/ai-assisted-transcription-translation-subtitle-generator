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
# Installed as a daily host cron line (see CLAUDE.md "Host setup
# checklist" -- a crontab is per-host and does NOT survive a migration):
#   0 3 * * * /opt/projects/subtitle-ai/scripts/backup_jobs_db.sh >> /opt/docker/appdata/subtitle-ai/backups/backup.log 2>&1
#
# Uses the sqlite3 CLI when present, otherwise python3's built-in
# sqlite3.Connection.backup() -- the same online-backup API, so equally
# safe against a live WAL database. Real gap (2026-09-28): a fresh Ubuntu
# host has python3 but not the sqlite3 CLI, so this script failed there.

set -eu

SRC_DB="${1:-/opt/docker/appdata/subtitle-ai/cache/jobs.db}"
BACKUP_DIR="${2:-/opt/docker/appdata/subtitle-ai/backups}"
RETAIN_DAYS=14

if [ ! -f "$SRC_DB" ]; then
    echo "backup_jobs_db.sh: source database not found: $SRC_DB" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
timestamp=$(date +%Y%m%d-%H%M%S)
dest="$BACKUP_DIR/jobs-$timestamp.db"

if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$SRC_DB" ".backup '$dest'"
else
    python3 - "$SRC_DB" "$dest" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close()
src.close()
PY
fi
gzip "$dest"

echo "backup_jobs_db.sh: wrote $dest.gz"

# Prune backups older than RETAIN_DAYS -- keeps the backup directory
# from growing unbounded on a host with no other retention policy.
find "$BACKUP_DIR" -name 'jobs-*.db.gz' -mtime "+$RETAIN_DAYS" -delete
