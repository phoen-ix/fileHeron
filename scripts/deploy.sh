#!/usr/bin/env bash
# fileHeron production deploy - pulls SemVer-tagged images from GHCR,
# with a source-build fallback for hosts that need to bootstrap before
# any release exists or want to ship a hotpatch ahead of cutting a
# release.
#
# Image source priority:
#   1. If `FH_TAG` is set in env / `.env`, that tag is pulled.
#   2. Otherwise pulls `:latest` (alias maintained by the release workflow).
#   3. If the pull fails (e.g., during initial bootstrap or a network
#      hiccup), falls back to building from source via the dev override
#      Dockerfile and tagging the result as the GHCR image name. The
#      next deploy will try GHCR again - local builds don't get sticky.
#
# Rollback: scripts/rollback.sh <previous-tag>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# Surface .env so FH_TAG (and friends) reach this script. compose reads
# .env directly; we read it for our own awareness when logging.
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

REPO_OWNER="phoen-ix"
# v1.0.0: updater is now `updater-shim` (perpetual) + `updater-executor`
# (ephemeral, never declared as a compose service - spawned ad-hoc by
# the shim per request).
IMAGES=(fileheron-backend fileheron-worker fileheron-frontend fileheron-updater-shim fileheron-updater-executor)
SERVICES=(backend worker frontend updater-shim)

echo "[deploy] target tag: $FH_TAG"
echo "[deploy] pulling images from ghcr.io/$REPO_OWNER/*:$FH_TAG"

PULL_OK=true
if ! docker compose pull "${SERVICES[@]}" 2>&1; then
    PULL_OK=false
fi

if [ "$PULL_OK" = "false" ]; then
    echo "[deploy] GHCR pull failed - falling back to local build"
    echo "[deploy] (this is normal on first bootstrap before any v* tag"
    echo "[deploy]  has been published; once a release is cut, subsequent"
    echo "[deploy]  deploys will pull from GHCR cleanly)"
    SHA="$(git rev-parse --short=12 HEAD)"
    # Use the prod Dockerfiles explicitly - the dev compose's Dockerfile.dev
    # has --reload / vite-dev CMDs that would be wrong for prod.
    docker build -f docker/backend/Dockerfile \
        --build-arg "FH_VERSION=local-$SHA" --build-arg "FH_GIT_SHA=$SHA" \
        -t "ghcr.io/$REPO_OWNER/fileheron-backend:$FH_TAG" .
    docker tag "ghcr.io/$REPO_OWNER/fileheron-backend:$FH_TAG" \
        "ghcr.io/$REPO_OWNER/fileheron-worker:$FH_TAG"
    docker build -f docker/frontend/Dockerfile \
        --build-arg "FH_VERSION=local-$SHA" --build-arg "FH_GIT_SHA=$SHA" \
        -t "ghcr.io/$REPO_OWNER/fileheron-frontend:$FH_TAG" .
    docker build -f docker/updater-shim/Dockerfile \
        --build-arg "FH_VERSION=local-$SHA" --build-arg "FH_GIT_SHA=$SHA" \
        -t "ghcr.io/$REPO_OWNER/fileheron-updater-shim:$FH_TAG" .
    docker build -f docker/updater-executor/Dockerfile \
        --build-arg "FH_VERSION=local-$SHA" --build-arg "FH_GIT_SHA=$SHA" \
        -t "ghcr.io/$REPO_OWNER/fileheron-updater-executor:$FH_TAG" .
fi

echo "[deploy] rolling services"
docker compose up -d "${SERVICES[@]}"

echo "[deploy] waiting for health (up to 90s)"
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
        echo "[deploy] healthy"
        break
    fi
    if [ "$(date +%s)" -gt "$DEADLINE" ]; then
        echo "[deploy] FAIL: healthcheck timeout -$UNHEALTHY" >&2
        echo "[deploy] container logs (last 20 lines):" >&2
        docker compose logs --tail=20 backend worker frontend >&2
        exit 1
    fi
    sleep 3
done

echo "[deploy] pruning old GHCR-namespaced images (keep last 5 per repo)"
for img in "${IMAGES[@]}"; do
    OLD=$(docker images "ghcr.io/$REPO_OWNER/$img" --format '{{.Tag}} {{.CreatedAt}}' \
        | grep -v '^latest ' \
        | sort -k2 \
        | head -n -5 \
        | awk '{print $1}')
    for t in $OLD; do
        docker rmi "ghcr.io/$REPO_OWNER/$img:$t" >/dev/null 2>&1 || true
        echo "  pruned ghcr.io/$REPO_OWNER/$img:$t"
    done
done

echo "[deploy] done - running on FH_TAG=$FH_TAG"
echo "[deploy] rollback: scripts/rollback.sh <previous-tag>"
