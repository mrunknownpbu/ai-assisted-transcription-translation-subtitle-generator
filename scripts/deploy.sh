#!/bin/sh
# Builds subtitle-ai:dev and deploys it to both the local (master) and
# remote (translate-server) hosts.
#
# Real gap this fixes (production-readiness audit, 2026-09-21): this
# exact sequence was hand-executed repeatedly across a long session with
# no script -- build, redeploy master, ship the image to the remote GPU
# host, redeploy it there, poll both health endpoints. Nothing here is
# new; it's the same sequence, now repeatable and not tribal knowledge.
#
# Usage: ./scripts/deploy.sh [remote-host]
# remote-host defaults to "media-server" (this deployment's real remote
# translate-server host, reachable via SSH config alias). Pass "" (or
# any falsy value via SKIP_REMOTE=1) to skip the remote leg entirely --
# useful if you only changed something that doesn't touch translate.py/
# translate_server.py.

set -eu

REMOTE_HOST="${1:-media-server}"
REMOTE_COMPOSE_DIR="/opt/docker/compose/subtitle-ai-translate-server"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Building subtitle-ai:dev"
cd "$REPO_ROOT"
docker build -t subtitle-ai:dev .

echo "==> Deploying master (subtitle-ai)"
docker compose up -d subtitle-ai

echo "==> Waiting for master health"
for _ in $(seq 1 30); do
    if curl -sf http://localhost:8099/api/health | grep -q '"ok":true'; then
        echo "    master healthy"
        break
    fi
    sleep 2
done

if [ -z "${SKIP_REMOTE:-}" ] && [ -n "$REMOTE_HOST" ]; then
    echo "==> Shipping image to $REMOTE_HOST"
    docker save subtitle-ai:dev | ssh "$REMOTE_HOST" docker load

    echo "==> Syncing compose.translate-server.yml to $REMOTE_HOST"
    ssh "$REMOTE_HOST" "mkdir -p $REMOTE_COMPOSE_DIR"
    scp compose.translate-server.yml "$REMOTE_HOST:$REMOTE_COMPOSE_DIR/compose.yml"

    echo "==> Redeploying translate-server on $REMOTE_HOST"
    ssh "$REMOTE_HOST" "cd $REMOTE_COMPOSE_DIR && docker compose up -d --force-recreate"

    echo "==> Waiting for translate-server health"
    for _ in $(seq 1 30); do
        if ssh "$REMOTE_HOST" "curl -sf http://localhost:8091/health" | grep -q '"status":"ok"'; then
            echo "    translate-server healthy"
            break
        fi
        sleep 2
    done
else
    echo "==> Skipping remote translate-server deploy (SKIP_REMOTE set or no host given)"
fi

echo "==> Done"
