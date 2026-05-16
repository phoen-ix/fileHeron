#!/usr/bin/env bash
# fileHeron rollback — re-tag a prior tag as :latest and roll.
#
# Usage:
#   scripts/rollback.sh                  # list available local image tags
#   scripts/rollback.sh <tag>            # roll back to that tag
#
# Works against the GHCR-namespaced images written by deploy.sh
# (`ghcr.io/phoen-ix/fileheron-*:<tag>`). Plain `docker compose build`
# without deploy.sh doesn't leave anything to roll back to.
#
# The tag can be either a SemVer release (e.g. `v0.2.0`) or a `local-*`
# build label written by deploy.sh's fallback path.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

REPO_OWNER="phoen-ix"
IMAGES=(fileheron-backend fileheron-worker fileheron-frontend)
SERVICES=(backend worker frontend)

# Resolve current FH_TAG so the new :latest reflects what compose will pull.
if [ -f .env ]; then
    set -o allexport
    # shellcheck disable=SC1091
    . ./.env
    set +o allexport
fi
FH_TAG="${FH_TAG:-latest}"
export FH_TAG

list_tags() {
    echo "Available image tags (most recent first):"
    for img in "${IMAGES[@]}"; do
        echo "  ghcr.io/$REPO_OWNER/$img:"
        docker images "ghcr.io/$REPO_OWNER/$img" --format '    {{.Tag}}  ({{.CreatedSince}})' \
            | grep -v "^    $FH_TAG " \
            | head -10
    done
}

if [ "${1:-}" = "" ]; then
    list_tags
    exit 0
fi

TARGET="$1"

# Verify all three repos have this tag locally.
MISSING=""
for img in "${IMAGES[@]}"; do
    if ! docker image inspect "ghcr.io/$REPO_OWNER/$img:$TARGET" >/dev/null 2>&1; then
        MISSING="$MISSING ghcr.io/$REPO_OWNER/$img:$TARGET"
    fi
done
if [ -n "$MISSING" ]; then
    echo "FATAL: missing image(s):$MISSING" >&2
    echo >&2
    list_tags >&2
    exit 2
fi

echo "[rollback] re-tagging $TARGET as :$FH_TAG for all three repos"
for img in "${IMAGES[@]}"; do
    docker tag "ghcr.io/$REPO_OWNER/$img:$TARGET" "ghcr.io/$REPO_OWNER/$img:$FH_TAG"
done

echo "[rollback] rolling services onto :$FH_TAG (= $TARGET)"
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
