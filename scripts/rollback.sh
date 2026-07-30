#!/usr/bin/env bash
# fileHeron rollback - re-tag a prior tag as :latest and roll.
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
IMAGES=(fileheron-backend fileheron-worker fileheron-frontend fileheron-updater-shim fileheron-updater-executor)
SERVICES=(backend worker frontend updater-shim)

# Resolve current FH_TAG so the new :latest reflects what compose will pull.
if [ -f .env ]; then
    set -o allexport
    # shellcheck disable=SC1091
    . ./.env
    set +o allexport
fi
FH_TAG="${FH_TAG:-latest}"
export FH_TAG

# Compose derives container names from ${COMPOSE_PROJECT_NAME:-fileheron} (see
# every `container_name:` in docker-compose.yml). Both scripts hardcoded the
# `fileheron-` prefix, so under any other project name - the `fileheron_drill`
# the restore drill uses, the `fileheron_e2e` CI uses, or any self-hoster who
# set one - `docker inspect` returned "missing" for 90 seconds and the script
# exited 1 on a SUCCESSFUL deploy (audit 2026-07-30).
container_name() {
    printf '%s-%s' "${COMPOSE_PROJECT_NAME:-fileheron}" "$1"
}

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

# Point .env at the target tag instead of re-tagging the target AS the current
# one. The re-tag approach rolled the running stack back correctly and then left
# `.env` still naming the broken version, so the next `docker compose pull` -
# or the next in-app Update's version comparison - silently re-pulled the thing
# we just rolled away from, overwriting the local re-tag. The rollback undid
# itself (audit 2026-07-30).
#
# install.sh's `set_kv` is the existing precedent for editing .env in place.
if [ -f .env ] && grep -qE '^FH_TAG=' .env; then
    echo "[rollback] pinning FH_TAG=$TARGET in .env (was $FH_TAG)"
    tmp="$(mktemp)"
    sed "s|^FH_TAG=.*|FH_TAG=$TARGET|" .env > "$tmp" && cat "$tmp" > .env && rm -f "$tmp"
elif [ -f .env ]; then
    echo "[rollback] appending FH_TAG=$TARGET to .env"
    printf '\nFH_TAG=%s\n' "$TARGET" >> .env
else
    echo "[rollback] WARNING: no .env found - FH_TAG not persisted, next pull may revert" >&2
fi
FH_TAG="$TARGET"
export FH_TAG

echo "[rollback] rolling services onto :$FH_TAG"
docker compose up -d "${SERVICES[@]}"

echo "[rollback] waiting for health (up to 90s)"
DEADLINE=$(($(date +%s) + 90))
while :; do
    UNHEALTHY=""
    for svc in backend frontend; do
        STATUS="$(docker inspect --format '{{.State.Health.Status}}' "$(container_name "$svc")" 2>/dev/null || echo missing)"
        if [ "$STATUS" != "healthy" ]; then
            UNHEALTHY="$UNHEALTHY $svc=$STATUS"
        fi
    done
    if [ -z "$UNHEALTHY" ]; then
        echo "[rollback] healthy on $TARGET"
        exit 0
    fi
    if [ "$(date +%s)" -gt "$DEADLINE" ]; then
        echo "[rollback] FAIL: healthcheck timeout -$UNHEALTHY" >&2
        docker compose logs --tail=20 backend worker frontend >&2
        exit 1
    fi
    sleep 3
done
