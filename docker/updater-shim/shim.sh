#!/bin/bash
# fileHeron updater-shim entry point.
#
# Polls /state/current_job.json. When status="pending", spawns the
# executor container matching the requested target_tag. Tracks
# in-flight jobs via the status field so a slow executor doesn't get
# double-spawned by the next poll tick.
#
# Trust model: filesystem-membership. Anything writing to /state is
# inside the compose project - the backend container or this shim
# itself. No HTTP, no HMAC, no port.
set -euo pipefail

STATE_FILE="${SHIM_STATE_FILE:-/state/current_job.json}"
STATE_DIR="$(dirname "$STATE_FILE")"
HOST_WORKSPACE="${UPDATER_HOST_WORKSPACE:-/opt/fileHeron}"
HOST_STATE="${UPDATER_HOST_STATE:-/opt/fileHeron/data/updater}"
GHCR_OWNER="${GHCR_OWNER:-phoen-ix}"
COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-fileheron}"
POLL_INTERVAL_SEC="${SHIM_POLL_INTERVAL_SEC:-5}"
# Mark stuck (in-flight for > this many seconds) jobs failed so the next
# /apply isn't blocked forever by a crashed executor. 1200s (20min) gives
# headroom for a failed update + self-healing auto-rollback, which runs a
# second ~90s health wait plus a stamp + compose up on top of the forward
# attempt and any slow image pulls.
STUCK_THRESHOLD_SEC="${SHIM_STUCK_THRESHOLD_SEC:-1200}"

mkdir -p "$STATE_DIR"

log() {
    printf '[shim %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%S)" "$*"
}

# Replace STATE_FILE atomically with $1, ensuring 0644 mode so the
# backend (uid 1000 appuser) can read it. Without this, mktemp's
# default 0600 + mv leaks through, every shim write silently breaks
# the backend's _read_state, and the backend logs a PermissionError
# traceback every poll tick. chmod *before* rename so there's no
# window where the file is 0600 and visible.
#
# The temp file MUST live in $STATE_DIR. `mktemp` with no argument creates it
# in the container's own /tmp, and /state is a bind mount - so `mv` was a
# CROSS-DEVICE move, which degrades to copy-then-unlink and is not atomic. The
# backend polls this file about once a second during an update, so it could
# read a half-copied file and fail to parse it (audit 2026-07-30,
# flow-selfupdate-9). Use shim_mktemp below rather than bare `mktemp`.
install_state() {
    chmod 0644 "$1"
    mv "$1" "$STATE_FILE"
}

# A temp file on the SAME filesystem as the state file, so install_state's mv
# is a rename.
shim_mktemp() {
    mktemp "$STATE_DIR/.state.XXXXXX"
}

log "fileheron-updater-shim starting (poll=${POLL_INTERVAL_SEC}s, ghcr=$GHCR_OWNER, project=$COMPOSE_PROJECT)"
log "workspace host=$HOST_WORKSPACE state host=$HOST_STATE"

# Resolve the project's internal docker network name once. Compose names
# networks "<project>_<network>", so the internal network ends up as
# e.g. "fileheron_internal". The executor needs to attach to it to reach
# the backend's healthcheck URL.
NETWORK_NAME="${COMPOSE_PROJECT}_internal"

# Mark any in-flight job as failed on startup - we just lost any
# tracking state, and the safer assumption is "executor was killed
# mid-run", not "executor is still happily running somewhere".
if [ -f "$STATE_FILE" ]; then
    status=$(jq -r '.status // ""' "$STATE_FILE" 2>/dev/null || echo "")
    # `claiming` belongs here too: the shim sets it, then blocks pulling the
    # executor image. A reboot inside that window left a job the startup sweep
    # skipped and the (inert) stuck detector never reached (audit #2).
    if [ "$status" = "claiming" ] || [ "$status" = "pulling" ] || [ "$status" = "running" ] || [ "$status" = "restarting" ] || [ "$status" = "rolling_back" ]; then
        log "found in-flight job (status=$status) on startup - marking failed"
        tmp=$(shim_mktemp)
        jq '. + {status: "failed", error: "shim restarted mid-job", finished_at: now | todate}' \
            "$STATE_FILE" > "$tmp" && install_state "$tmp"
    fi
fi

while true; do
    sleep "$POLL_INTERVAL_SEC"

    [ -f "$STATE_FILE" ] || continue

    status=$(jq -r '.status // ""' "$STATE_FILE" 2>/dev/null || echo "")

    case "$status" in
        pending)
            target_tag=$(jq -r '.target_tag // ""' "$STATE_FILE")
            job_id=$(jq -r '.id // ""' "$STATE_FILE")
            action=$(jq -r '.action // "update"' "$STATE_FILE")
            if [ -z "$target_tag" ]; then
                log "ERROR pending job has no target_tag - marking failed"
                tmp=$(shim_mktemp)
                jq '. + {status: "failed", error: "missing target_tag", finished_at: now | todate}' \
                    "$STATE_FILE" > "$tmp" && install_state "$tmp"
                continue
            fi

            log "claiming job=$job_id action=$action target_tag=$target_tag"
            # Claim atomically: if another shim instance somehow exists,
            # only one flips status from pending → claiming first.
            tmp=$(shim_mktemp)
            jq --arg now "$(date -u +%Y-%m-%dT%H:%M:%S)" \
               '. + {status: "claiming", claimed_at: $now}' \
               "$STATE_FILE" > "$tmp" && install_state "$tmp"

            executor_image="ghcr.io/$GHCR_OWNER/fileheron-updater-executor:$target_tag"

            # Pull the executor for this tag. If the registry is
            # unreachable or the tag doesn't exist, fail the job here
            # before spawning anything.
            log "pulling $executor_image"
            if ! docker pull "$executor_image"; then
                log "pull failed; marking job failed"
                tmp=$(shim_mktemp)
                jq --arg err "executor pull failed: $executor_image" \
                   --arg now "$(date -u +%Y-%m-%dT%H:%M:%S)" \
                   '. + {status: "failed", error: $err, finished_at: $now}' \
                   "$STATE_FILE" > "$tmp" && install_state "$tmp"
                continue
            fi

            # Spawn the executor. The shim BLOCKS on this - only one
            # job runs at a time, no concurrent updates.
            log "spawning executor for $target_tag"
            container_name="${COMPOSE_PROJECT}-executor-$(date +%s)"
            exit_code=0
            # COMPOSE_HOST_ROOT is the critical bit: compose substitutes
            # this into the bind-mount sources so the daemon resolves
            # them against the HOST filesystem (the host's compose dir)
            # rather than against the executor container's /workspace.
            # Without it, every relative `./data/X` mount would auto-
            # create empty shadow dirs at `/workspace/data/X` on the
            # host and silently fork the data layer.
            docker run --rm \
                --name "$container_name" \
                --network "$NETWORK_NAME" \
                -v /var/run/docker.sock:/var/run/docker.sock \
                -v "$HOST_WORKSPACE:/workspace" \
                -v "$HOST_STATE:/state" \
                -e "COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT" \
                -e "COMPOSE_HOST_ROOT=$HOST_WORKSPACE" \
                -e "GHCR_OWNER=$GHCR_OWNER" \
                "$executor_image" \
                || exit_code=$?

            log "executor exited (code=$exit_code)"
            # The executor updates status itself (healthy/failed/etc.)
            # via the state file. We don't override what it wrote - it
            # has more context than we do about WHY it failed.
            # The only safety net: if the executor died without writing
            # a terminal status, mark it failed here.
            if [ -f "$STATE_FILE" ]; then
                final=$(jq -r '.status // ""' "$STATE_FILE")
                if [ "$final" != "healthy" ] && [ "$final" != "failed" ] && [ "$final" != "rolled_back" ]; then
                    log "executor exited without terminal status; marking failed"
                    tmp=$(shim_mktemp)
                    jq --arg err "executor crashed (exit $exit_code) without writing status" \
                       --arg now "$(date -u +%Y-%m-%dT%H:%M:%S)" \
                       '. + {status: "failed", error: $err, finished_at: $now}' \
                       "$STATE_FILE" > "$tmp" && install_state "$tmp"
                fi
            fi
            ;;

        pulling|running|restarting|claiming|rolling_back)
            # In-flight. Watch for stuck executors (executor crashed
            # mid-run without us seeing the exit). If `started_at` is
            # older than the threshold, mark failed.
            started_raw=$(jq -r '.started_at // .claimed_at // ""' "$STATE_FILE")
            if [ -n "$started_raw" ]; then
                # busybox `date -d` cannot parse an ISO 8601 timestamp with a
                # `T` - it answers "invalid date" for every value the updater
                # writes, so this evaluated to 0 and the detector never fired
                # once. A job interrupted while `claiming` (host reboot during
                # the executor image pull) then stayed in-flight forever: every
                # Update and Rollback returned 409 UPDATE_IN_PROGRESS and the
                # only recovery was hand-deleting a JSON file no doc mentions
                # (audit #2). busybox needs the format via -D; GNU date is the
                # fallback for anyone running this outside the alpine image.
                started_clean=${started_raw%%.*}
                started_clean=${started_clean%Z}
                started_epoch=$(date -D "%Y-%m-%dT%H:%M:%S" -d "$started_clean" +%s 2>/dev/null \
                                || date -d "$started_clean" +%s 2>/dev/null \
                                || echo 0)
                now_epoch=$(date -u +%s)
                if [ "$started_epoch" -gt 0 ] && [ $((now_epoch - started_epoch)) -gt "$STUCK_THRESHOLD_SEC" ]; then
                    log "job in-flight for > ${STUCK_THRESHOLD_SEC}s - marking failed"
                    tmp=$(shim_mktemp)
                    jq --arg now "$(date -u +%Y-%m-%dT%H:%M:%S)" \
                       '. + {status: "failed", error: "stuck (no progress)", finished_at: $now}' \
                       "$STATE_FILE" > "$tmp" && install_state "$tmp"
                fi
            fi
            ;;

        healthy|failed|rolled_back|"")
            : # nothing to do - terminal or empty
            ;;

        *)
            log "WARN unknown status: $status"
            ;;
    esac
done
