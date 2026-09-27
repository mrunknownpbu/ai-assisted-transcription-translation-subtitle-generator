#!/bin/sh
# Builds subtitle-ai:dev and redeploys the local subtitle-ai container,
# plus (only when a host is passed) a remote translate-server.
#
# Real gap this fixes (production-readiness audit, 2026-09-21): this
# exact sequence was hand-executed repeatedly across a long session with
# no script -- build, redeploy master, ship the image to the remote GPU
# host, redeploy it there, poll both health endpoints. Nothing here is
# new; it's the same sequence, now repeatable and not tribal knowledge.
#
# Usage: ./scripts/deploy.sh [remote-host]
# Local-only by default (no remote-host argument, SKIP_REMOTE unset): a
# fresh checkout on a new host must never try to ship an image to, and
# redeploy, an old deployment's remote translate-server just because
# nobody passed an argument. Pass a remote host (an SSH config alias,
# e.g. "media-server") to opt in to that leg; SKIP_REMOTE=1 still forces
# it off even if one is given.

set -eu

REMOTE_HOST="${1:-}"
REMOTE_COMPOSE_DIR="/opt/docker/compose/subtitle-ai-translate-server"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Building subtitle-ai:dev"
cd "$REPO_ROOT"
docker build -t subtitle-ai:dev .

echo "==> Deploying master (subtitle-ai)"
docker compose up -d subtitle-ai

# A timed-out health wait must fail the deploy. Before 2026-09-28 both
# loops below just fell through after 30 tries and the script still
# printed "Done" and exited 0 on a container that never came up.
echo "==> Waiting for master health"
healthy=""
for _ in $(seq 1 30); do
    if curl -sf http://localhost:8099/api/health | grep -q '"ok":true'; then
        echo "    master healthy"
        healthy=1
        break
    fi
    sleep 2
done
if [ -z "$healthy" ]; then
    echo "!!  master did not become healthy within 60s; last log lines:" >&2
    docker logs --tail 50 subtitle-ai >&2 || true
    exit 1
fi

if [ -z "${SKIP_REMOTE:-}" ] && [ -n "$REMOTE_HOST" ]; then
    echo "==> Shipping image to $REMOTE_HOST"
    docker save subtitle-ai:dev | ssh "$REMOTE_HOST" docker load

    echo "==> Syncing compose.translate-server.yml to $REMOTE_HOST"
    ssh "$REMOTE_HOST" "mkdir -p $REMOTE_COMPOSE_DIR"
    scp compose.translate-server.yml "$REMOTE_HOST:$REMOTE_COMPOSE_DIR/compose.yml"

    echo "==> Redeploying translate-server on $REMOTE_HOST"
    ssh "$REMOTE_HOST" "cd $REMOTE_COMPOSE_DIR && docker compose up -d --force-recreate"

    echo "==> Waiting for translate-server health"
    healthy=""
    for _ in $(seq 1 30); do
        if ssh "$REMOTE_HOST" "curl -sf http://localhost:8091/health" | grep -q '"status":"ok"'; then
            echo "    translate-server healthy"
            healthy=1
            break
        fi
        sleep 2
    done
    if [ -z "$healthy" ]; then
        echo "!!  translate-server on $REMOTE_HOST did not become healthy within 60s" >&2
        exit 1
    fi
else
    echo "==> Skipping remote translate-server deploy (SKIP_REMOTE set or no host given)"
fi

echo "==> Done"
