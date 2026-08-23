#!/usr/bin/env bash
# fileHeron production deploy - pulls SemVer-tagged images from GHCR,
# with a source-build fallback for hosts that need to bootstrap before
# any release exists or want to ship a hotpatch ahead of cutting a
# release.
#
# Image source priority:
#   1. `FH_TAG` from the ENVIRONMENT, which beats .env (matching docker
#      compose's own precedence). `FH_TAG=v1.2.3 scripts/deploy.sh` works.
#   2. `FH_TAG` from `.env`.
#   3. Otherwise pulls `:latest` (alias maintained by the release workflow).
#
# If the pull fails:
#   - and every image is already local at that tag, it proceeds with those
#     and builds nothing (the normal case for retrying a deploy);
#   - and FH_TAG is a published release (`vX.Y.Z`), it FAILS (exit 3) rather
#     than building. A source build is not that release, and tagging it as
#     one would overwrite the image rollback depends on;
#   - otherwise it falls back to building from source, which is what makes
#     bootstrapping a host before any release exists possible. Local builds
#     don't get sticky - the next deploy tries GHCR again.
#
# Rollback: scripts/rollback.sh <previous-tag>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# Surface .env so FH_TAG (and friends) reach this script. compose reads
# .env directly; we read it for our own awareness when logging.
#
# A caller-supplied FH_TAG MUST win over .env. `set -o allexport` + `.` assigns
# unconditionally, so sourcing clobbered it and `FH_TAG=v2.15.0 scripts/deploy.sh`
# silently deployed whatever .env already said - a no-op that reports success,
# which is the worst possible failure for a deploy tool. The header three lines
# up has promised env-beats-.env since this script was written, and docker
# compose itself resolves it that way (shell environment beats the .env file),
# so the script disagreed with both its own documentation and the tool it drives.
FH_TAG_FROM_CALLER="${FH_TAG-}"
FH_TAG_CALLER_SET="${FH_TAG+set}"

if [ -f .env ]; then
    set -o allexport
    # shellcheck disable=SC1091
    . ./.env
    set +o allexport
fi

# `+set` not `-n`: an exported-but-EMPTY FH_TAG is a caller value too (it comes
# out of `FH_TAG="$(some_lookup)"` returning nothing), and collapsing it into
# "unset" silently fell through to .env. Compose resolves an empty one to the
# default, so match that.
# Capture what .env said BEFORE the override, or the drift check below compares
# the caller's value against itself and can never fire.
FH_TAG_FROM_DOTENV="${FH_TAG:-}"
if [ -n "${FH_TAG_CALLER_SET:-}" ]; then
    FH_TAG="$FH_TAG_FROM_CALLER"
fi
FH_TAG="${FH_TAG:-latest}"
export FH_TAG

# The caller's tag is NOT written to .env, and .env is what everything else
# reads: the documented `docker compose up -d` would revert the stack, and
# updater-executor's read_current_tag() uses it as the auto-rollback anchor - so
# a failed in-app update would roll to a version this host was never running.
# Persisting it here would be a surprising side effect of a deploy, so say so
# instead and let the operator decide.
# Compare against what COMPOSE would resolve from .env alone - every image line
# in docker-compose.yml is `${FH_TAG:-latest}`, so an absent FH_TAG means
# `latest`, not "nothing". Comparing against the raw value instead would print
# this warning on every `latest` deploy of a host that simply has no FH_TAG
# line, where nothing would revert at all.
DOTENV_EFFECTIVE_TAG="${FH_TAG_FROM_DOTENV:-latest}"
if [ -f .env ] && [ "$FH_TAG" != "$DOTENV_EFFECTIVE_TAG" ]; then
    echo "[deploy] NOTE: deploying :$FH_TAG but .env resolves to :$DOTENV_EFFECTIVE_TAG"
    echo "[deploy] A later 'docker compose up -d' would revert to that, and the"
    echo "[deploy] in-app updater reads it as the rollback anchor. Update .env"
    echo "[deploy] if this deploy is meant to stick."
fi

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

# The compose services this script rolls. Every one of them runs an image named
# `fileheron-<service>`, so the image list DERIVES from this rather than being a
# second hand-written list beside it. It used to be two lists, and they drifted:
# rollback.sh preflighted all five images while rolling only these four, so a
# missing updater-executor blocked rollback entirely (see rollback.sh).
SERVICES=(backend worker frontend updater-shim)

service_image() {
    printf 'ghcr.io/%s/fileheron-%s' "$REPO_OWNER" "$1"
}

# v1.0.0: updater is now `updater-shim` (perpetual) + `updater-executor`
# (ephemeral, never declared as a compose service - spawned ad-hoc by
# the shim per request, which pulls it itself). Pruned alongside the rest;
# never required for a deploy or a rollback to succeed.
OPTIONAL_IMAGES=(fileheron-updater-executor)

IMAGES=()
for _svc in "${SERVICES[@]}"; do IMAGES+=("fileheron-$_svc"); done
IMAGES+=("${OPTIONAL_IMAGES[@]}")
unset _svc

echo "[deploy] target tag: $FH_TAG"
echo "[deploy] pulling images from ghcr.io/$REPO_OWNER/*:$FH_TAG"

PULL_OK=true
if ! docker compose pull "${SERVICES[@]}" 2>&1; then
    PULL_OK=false
fi

# Is this a tag GHCR is expected to serve? That is the whole question, because
# it decides whether a failed pull is "the registry is having a moment" or
# "there is nothing published under this name and building is the point".
#
# `latest` belongs here and missing it was a real hole: it is a CI-maintained
# alias (server-release.yml's publish-latest job), it is the SHIPPED DEFAULT
# (.env.example, install.sh), and it is exempt from the prune below - so on a
# stock self-host it is the ONLY local rollback anchor there is. A guard that
# protected `vX.Y.Z` and not `latest` protected the configuration almost nobody
# runs and left the default one exposed. `dev-<sha>` is published too.
is_published_tag() {
    case "$1" in
        latest|dev-*) return 0 ;;
    esac
    printf '%s' "$1" | grep -qE '^v[0-9]+\.[0-9]+\.[0-9]+$'
}

if [ "$PULL_OK" = "false" ] && is_published_tag "$FH_TAG"; then
    # A published tag must come from GHCR. If every image is already here at it,
    # a transient registry failure needs nothing at all - retrying a deploy of a
    # tag you already have is the common case, and it is why this branch exists.
    HAVE_ALL=true
    for svc in "${SERVICES[@]}"; do
        if ! docker image inspect "$(service_image "$svc"):$FH_TAG" >/dev/null 2>&1; then
            HAVE_ALL=false
        fi
    done
    if [ "$HAVE_ALL" = "true" ]; then
        echo "[deploy] GHCR pull failed, but every image is already local at :$FH_TAG"
        echo "[deploy] proceeding with the images on disk - NOT rebuilding"
        PULL_OK=true
    else
        # The fallback tags its build `ghcr.io/<owner>/fileheron-<svc>:$FH_TAG`,
        # i.e. it overwrites the published image UNDER ITS OWN NAME. Destructive
        # twice over: the running stack silently becomes working-tree code
        # wearing a published label, and the genuine image - the only local
        # thing rollback.sh can return to - is gone. One flaky pull is all it
        # takes. Requiring ALL FOUR also stops a partial pull rolling a
        # mixed-version stack.
        echo "[deploy] FATAL: could not pull published tag :$FH_TAG from GHCR," >&2
        echo "[deploy] and not every image is present locally at it." >&2
        echo "[deploy] Refusing to build from source: a local build is NOT that" >&2
        echo "[deploy] release, and tagging it so would overwrite the published" >&2
        echo "[deploy] image that rollback depends on." >&2
        echo "[deploy] Fix registry access and retry, or deploy under a local tag" >&2
        echo "[deploy] (e.g. FH_TAG=local-\$(git rev-parse --short=12 HEAD))." >&2
        exit 3
    fi
fi

# Everything below is the NON-published-tag path, and it deliberately has no
# short-circuit. A `local-*` tag can never be pulled, so `PULL_OK` is always
# false for it - short-circuiting on "the images are already here" would mean
# run 1 builds, run 2 silently ships run 1's binaries, and every edit after that
# never reaches the stack while the tool prints "done". That is the same
# deploys-the-wrong-thing-and-reports-success failure this file exists to
# prevent, and it would falsify the header's "local builds don't get sticky".

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
    # `grep -v` exits 1 when it filters everything away, and under
    # `set -o pipefail` that killed the whole script - AFTER a fully successful
    # deploy, so it printed "healthy" and then exited 1 with no "done" line.
    # Reached whenever a repo has no local images (the executor's normal state)
    # or only a `latest` row (a stock fresh self-host). Same in rollback.sh.
    OLD=$(docker images "ghcr.io/$REPO_OWNER/$img" --format '{{.Tag}} {{.CreatedAt}}' \
        | { grep -v '^latest ' || true; } \
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
