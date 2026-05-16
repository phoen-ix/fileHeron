#!/usr/bin/env bash
# fileHeron production deploy — SHA-tagged image build + rolling restart.
#
# Why: `docker compose build && up -d` is fine until something breaks at 9am.
# Rolling back without SHA-tagged images means rebuilding from a prior git
# commit — slow, error-prone, and dependent on the historical build still
# working against current deps.
#
# This script:
#   1. Builds backend/worker/frontend with the current git SHA stamped into
#      the image labels.
#   2. Tags each image both `:latest` and `:<sha>` so the prior `:latest`
#      lives on under its SHA tag.
#   3. Rolls the running services onto the new images.
#   4. Waits for healthchecks to go green.
#   5. Prunes images older than the last 5 SHA tags per repo so disk
#      doesn't fill.
#
# Rollback: scripts/rollback.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

SHA="$(git rev-parse --short=12 HEAD)"
DIRTY=""
if ! git diff --quiet || ! git diff --cached --quiet; then
    DIRTY="-dirty"
    echo "[deploy] WARNING: working tree has uncommitted changes; tagging $SHA$DIRTY"
fi
TAG="$SHA$DIRTY"

SERVICES=(backend worker frontend)
IMAGES=(fileheron-backend fileheron-worker fileheron-frontend)

echo "[deploy] building images tagged :$TAG"
docker compose build "${SERVICES[@]}"

echo "[deploy] applying SHA tags"
for img in "${IMAGES[@]}"; do
    if docker image inspect "$img:latest" >/dev/null 2>&1; then
        docker tag "$img:latest" "$img:$TAG"
        echo "  $img:latest -> $img:$TAG"
    fi
done

echo "[deploy] rolling services"
docker compose up -d "${SERVICES[@]}"

echo "[deploy] waiting for health (up to 90s)"
DEADLINE=$(($(date +%s) + 90))
while :; do
    UNHEALTHY=""
    for svc in backend frontend; do  # worker has its own healthcheck post-Wave-1
        STATUS="$(docker inspect --format '{{.State.Health.Status}}' "fileheron-$svc" 2>/dev/null || echo missing)"
        if [ "$STATUS" != "healthy" ]; then
            UNHEALTHY="$UNHEALTHY $svc=$STATUS"
        fi
    done
    if [ -z "$UNHEALTHY" ]; then
        echo "[deploy] healthy"
        break
    fi
    if [ "$(date +%s)" -gt "$DEADLINE" ]; then
        echo "[deploy] FAIL: healthcheck timeout —$UNHEALTHY" >&2
        echo "[deploy] container logs (last 20 lines):" >&2
        docker compose logs --tail=20 backend worker frontend >&2
        exit 1
    fi
    sleep 3
done

echo "[deploy] pruning old SHA-tagged images (keep last 5 per repo)"
for img in "${IMAGES[@]}"; do
    # Find SHA-tagged images for this repo (skip :latest), sorted by creation, oldest at top.
    # Keep last 5; delete the rest.
    OLD=$(docker images "$img" --format '{{.Tag}} {{.CreatedAt}}' \
        | grep -v '^latest ' \
        | sort -k2 \
        | head -n -5 \
        | awk '{print $1}')
    for t in $OLD; do
        docker rmi "$img:$t" >/dev/null 2>&1 || true
        echo "  pruned $img:$t"
    done
done

echo "[deploy] done — running on :$TAG"
echo "[deploy] rollback: scripts/rollback.sh <previous-sha>"
