# shellcheck shell=bash
# Shared by scripts/deploy.sh and scripts/rollback.sh - sourced, never run.
# The two carried these definitions and the health wait line for line, and the
# image list once drifted between them (audit 2026-11-08).

REPO_OWNER="${GHCR_OWNER:-phoen-ix}"

# The compose services both scripts roll. Every one runs an image named
# `fileheron-<service>`, so the image list DERIVES from this rather than being a
# second hand-written list beside it: rollback.sh once preflighted all five
# images while rolling only these four, so a missing updater-executor blocked
# rollback entirely.
SERVICES=(backend worker frontend updater-shim)

service_image() {
    printf 'ghcr.io/%s/fileheron-%s' "$REPO_OWNER" "$1"
}

# Never a compose service and never required for a deploy or a rollback: the
# shim pulls it per update (docker/updater-shim/shim.sh) and fails that job
# cleanly if it cannot. Listed so pruning and `list_tags` cover it.
OPTIONAL_IMAGES=(fileheron-updater-executor)

IMAGES=()
for _svc in "${SERVICES[@]}"; do IMAGES+=("fileheron-$_svc"); done
IMAGES+=("${OPTIONAL_IMAGES[@]}")
unset _svc

# Compose derives container names from ${COMPOSE_PROJECT_NAME:-fileheron} (see
# every `container_name:` in docker-compose.yml). Both scripts hardcoded the
# `fileheron-` prefix, so under any other project name - the `fileheron_drill`
# the restore drill uses, the `fileheron_e2e` CI uses, or any self-hoster who
# set one - `docker inspect` returned "missing" for 90 seconds and the script
# exited 1 on a SUCCESSFUL deploy (audit 2026-07-30).
container_name() {
    printf '%s-%s' "${COMPOSE_PROJECT_NAME:-fileheron}" "$1"
}

# Wait up to 90 s for backend and frontend to report healthy. $1 is the log
# prefix. Returns 0 when healthy; on timeout prints the unhealthy ones and the
# last log lines and returns 1.
wait_healthy() {
    local prefix="$1" deadline unhealthy svc status
    echo "[$prefix] waiting for health (up to 90s)"
    deadline=$(($(date +%s) + 90))
    while :; do
        unhealthy=""
        for svc in backend frontend; do
            status="$(docker inspect --format '{{.State.Health.Status}}' "$(container_name "$svc")" 2>/dev/null || echo missing)"
            if [ "$status" != "healthy" ]; then
                unhealthy="$unhealthy $svc=$status"
            fi
        done
        if [ -z "$unhealthy" ]; then
            return 0
        fi
        if [ "$(date +%s)" -gt "$deadline" ]; then
            echo "[$prefix] FAIL: healthcheck timeout -$unhealthy" >&2
            echo "[$prefix] container logs (last 20 lines):" >&2
            docker compose logs --tail=20 backend worker frontend >&2
            return 1
        fi
        sleep 3
    done
}
