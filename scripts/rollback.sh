#!/usr/bin/env bash
# fileHeron rollback — re-tag a prior SHA-tagged image as :latest and roll.
#
# Usage:
#   scripts/rollback.sh                  # list available SHA tags
#   scripts/rollback.sh <sha>            # roll back to that SHA
#
# Requires that scripts/deploy.sh built the prior image with SHA tagging.
# Plain `docker compose build` (without deploy.sh) doesn't leave anything
# to roll back to.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

IMAGES=(fileheron-backend fileheron-worker fileheron-frontend)
SERVICES=(backend worker frontend)

list_tags() {
    echo "Available SHA-tagged images (most recent first):"
    for img in "${IMAGES[@]}"; do
        echo "  $img:"
        docker images "$img" --format '    {{.Tag}}  ({{.CreatedSince}})' \
            | grep -v '^    latest ' \
            | head -10
    done
}

if [ "${1:-}" = "" ]; then
    list_tags
    exit 0
fi

TARGET="$1"

# Verify all three repos have this tag.
MISSING=""
for img in "${IMAGES[@]}"; do
    if ! docker image inspect "$img:$TARGET" >/dev/null 2>&1; then
        MISSING="$MISSING $img:$TARGET"
    fi
done
if [ -n "$MISSING" ]; then
    echo "FATAL: missing image(s):$MISSING" >&2
    echo >&2
    list_tags >&2
    exit 2
fi

echo "[rollback] re-tagging $TARGET as :latest for all three repos"
for img in "${IMAGES[@]}"; do
    docker tag "$img:$TARGET" "$img:latest"
done

echo "[rollback] rolling services onto :latest (= $TARGET)"
docker compose up -d "${SERVICES[@]}"

echo "[rollback] waiting for health (up to 90s)"
DEADLINE=$(($(date +%s) + 90))
while :; do
    UNHEALTHY=""
    for svc in backend frontend; do
        STATUS="$(docker inspect --format '{{.State.Health.Status}}' "fileheron-$svc" 2>/dev/null || echo missing)"
        if [ "$STATUS" != "healthy" ]; then
            UNHEALTHY="$UNHEALTHY $svc=$STATUS"
        fi
    done
    if [ -z "$UNHEALTHY" ]; then
        echo "[rollback] healthy on $TARGET"
        exit 0
    fi
    if [ "$(date +%s)" -gt "$DEADLINE" ]; then
        echo "[rollback] FAIL: healthcheck timeout —$UNHEALTHY" >&2
        docker compose logs --tail=20 backend worker frontend >&2
        exit 1
    fi
    sleep 3
done
