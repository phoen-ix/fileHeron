#!/usr/bin/env bash
# fileHeron rollback - pin FH_TAG to a prior tag in .env and roll onto it.
#
# (It used to re-tag the target as `:latest` instead. That rolled the stack back
# correctly and left `.env` still naming the broken version, so the next pull
# silently undid the rollback - see the comment above the .env edit below. The
# header said "re-tag as :latest" for four releases after that changed, which is
# what an operator reads mid-incident.)
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

# Same derivation as deploy.sh: the image list comes FROM the service list.
# These were two hand-written lists, and the preflight below looped the wrong
# one - see the comment there for what that cost.
SERVICES=(backend worker frontend updater-shim)

service_image() {
    printf 'ghcr.io/%s/fileheron-%s' "$REPO_OWNER" "$1"
}

# Never a compose service and never required for a rollback: the shim pulls it
# per update (docker/updater-shim/shim.sh) and fails that job cleanly if it
# cannot. Listed only so `list_tags` shows it.
OPTIONAL_IMAGES=(fileheron-updater-executor)

IMAGES=()
for _svc in "${SERVICES[@]}"; do IMAGES+=("fileheron-$_svc"); done
IMAGES+=("${OPTIONAL_IMAGES[@]}")
unset _svc

# Resolve the CURRENT FH_TAG - only so the "(was ...)" message and list_tags'
# filter can name it. The tag being rolled TO is $1, never this.
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
        # `|| true`: grep exits 1 when it filters everything away, and under
        # `set -o pipefail` that aborted the listing mid-way and exited 1 - so
        # `scripts/rollback.sh` with no args, the FIRST command anyone runs in
        # an incident, died on a repo with no local images. It also turned the
        # documented `exit 2` for a missing image into a bare exit 1.
        docker images "ghcr.io/$REPO_OWNER/$img" --format '    {{.Tag}}  ({{.CreatedSince}})' \
            | { grep -v "^    $FH_TAG " || true; } \
            | head -10
    done
}

if [ "${1:-}" = "" ]; then
    list_tags
    exit 0
fi

TARGET="$1"

# Every image the four SERVICES run must be present locally - those are what
# this script actually rolls onto.
#
# This loop used to iterate IMAGES, which is five: it included
# updater-executor, an image no normal update ever leaves on the host (the shim
# pulls it per run and `docker run --rm` takes the container with it). So on any
# host that had not hand-pulled it, the preflight exit 2'd and rolled back
# NOTHING - the emergency path was unavailable precisely when it was needed, and
# it reported a missing image as the reason, which reads like a real blocker.
# Measured on the reference host 2026-08-23: four of five present, rollback
# inoperative, and nothing had ever noticed because rollback is rarely run.
#
# It fails safe (this precedes the .env edit below), so nothing was corrupted -
# it simply did not work.
MISSING=""
for svc in "${SERVICES[@]}"; do
    img="$(service_image "$svc"):$TARGET"
    if ! docker image inspect "$img" >/dev/null 2>&1; then
        MISSING="$MISSING $img"
    fi
done
if [ -n "$MISSING" ]; then
    echo "FATAL: missing image(s):$MISSING" >&2
    echo >&2
    list_tags >&2
    exit 2
fi

# Advisory. Absent is the NORMAL state; the shim will pull it when an in-app
# update or rollback next needs it. Never a reason to block.
for img in "${OPTIONAL_IMAGES[@]}"; do
    if ! docker image inspect "ghcr.io/$REPO_OWNER/$img:$TARGET" >/dev/null 2>&1; then
        echo "[rollback] note: ghcr.io/$REPO_OWNER/$img:$TARGET is not local;" \
             "the in-app updater pulls it on demand, so this does not block."
    fi
done

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
