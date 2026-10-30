#!/bin/bash
# fileHeron updater-shim entry point.
#
# Polls /state/current_job.json. When status="pending", checks the job, verifies
# the signature of the executor image matching the requested target_tag and
# spawns that executor by its verified digest. Tracks in-flight jobs via the
# status field so a slow executor doesn't get double-spawned by the next poll
# tick.
#
# Trust model: /state is written by the backend and worker containers (uid 1000)
# and is treated as ATTACKER-CONTROLLED - a compromised web app can write every
# byte of it. This shim is the enforcement point, because it alone decides which
# executor image runs, as root, with the host docker socket: it validates the
# job's shape, refuses an "update" to an older version than the one running
# (read from the docker daemon, never from the job), binds a "rollback" to the
# version it recorded itself (in /private, which nothing else mounts), and runs
# only an executor image signed by this repository's release workflow. No HTTP,
# no HMAC, no port.
set -euo pipefail

STATE_FILE="${SHIM_STATE_FILE:-/state/current_job.json}"
STATE_DIR="$(dirname "$STATE_FILE")"
# The shim's own memory, mounted by nothing else: the version that was running
# before the last successful job, which is the only legitimate Rollback target.
PRIVATE_DIR="${SHIM_PRIVATE_DIR:-/private}"
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
# Liveness for the compose healthcheck. The file holds an epoch the shim
# promises to be alive until: a poll pass extends it by HEARTBEAT_GRACE_SEC,
# and each step that legitimately blocks (the signature check, the executor
# pull, the executor run) extends it by STUCK_THRESHOLD_SEC first - so a long
# update never reads as a hang, while a shim wedged anywhere else goes
# unhealthy within the grace. It lives in the container's own /tmp, not in the
# /state dir the backend shares.
HEARTBEAT_FILE="${SHIM_HEARTBEAT_FILE:-/tmp/shim-heartbeat}"
HEARTBEAT_GRACE_SEC="${SHIM_HEARTBEAT_GRACE_SEC:-30}"
# Image signatures (verify_image below). The images are signed by
# server-release.yml with cosign (keyless, GitHub's OIDC issuer), and the
# signing certificate names the workflow that ran: this repository's release
# workflow on a `v*` tag. A leaked registry token cannot produce that identity;
# only a tag pushed to the repository can. A fork that renamed the repository
# overrides the identity; a fork that does not sign sets
# SHIM_REQUIRE_SIGNATURE=false and is told on every job that it then trusts
# its registry account alone.
# Only an explicit false/0/no switches verification off; any other value,
# including a typo, keeps it on.
case "$(printf '%s' "${SHIM_REQUIRE_SIGNATURE:-true}" | tr '[:upper:]' '[:lower:]')" in
    false|0|no) REQUIRE_SIGNATURE=false ;;
    *) REQUIRE_SIGNATURE=true ;;
esac
SIGNER_IDENTITY_RE="${SHIM_SIGNER_IDENTITY_RE:-^https://github\\.com/${GHCR_OWNER}/fileHeron/\\.github/workflows/server-release\\.yml@refs/tags/v[0-9]+\\.[0-9]+\\.[0-9]+\$}"
OIDC_ISSUER="https://token.actions.githubusercontent.com"
VERIFY_OUT="${TMPDIR:-/tmp}/cosign-verify.out"
VERIFY_LOG="${TMPDIR:-/tmp}/cosign-verify.err"
EXECUTOR_MEM_LIMIT="${UPDATER_EXECUTOR_MEM_LIMIT:-512m}"

mkdir -p "$STATE_DIR"

# Never fatal: under `set -e` a failed write here would stop the very loop it
# reports on. Atomic (same-directory rename) so the healthcheck never reads a
# half-written number.
heartbeat() {
    local until=$(( $(date -u +%s) + ${1:-$HEARTBEAT_GRACE_SEC} ))
    { printf '%s\n' "$until" > "$HEARTBEAT_FILE.tmp" && mv "$HEARTBEAT_FILE.tmp" "$HEARTBEAT_FILE"; } 2>/dev/null || true
}
heartbeat

log() {
    printf '[shim %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%S)" "$*"
}

# As PID 1 this script gets no default signal handling: without a trap SIGTERM
# does nothing, and every `docker stop` of this container waited out Docker's
# 10s grace for the SIGKILL - on every update, because the executor recreates
# the shim as its last step. A trap alone is not enough either: bash runs it
# only once the FOREGROUND child returns, and at that moment the child is the
# executor's `docker run`, blocked on the very compose command recreating this
# container. So each blocking step below is `cmd & wait $!`, which a trapped
# signal interrupts at once. Exiting mid-step changes nothing the SIGKILL did
# not already do 10s later: the executor is a sibling container the daemon
# keeps running without our docker CLI, a job left in flight is failed by the
# next shim's startup sweep, and every state write is a rename.
on_signal() {
    log "received $1 - exiting"
    exit 0
}
trap 'on_signal SIGTERM' TERM
trap 'on_signal SIGINT' INT

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
# is a rename. mktemp creates it exclusively, so a name planted in the
# backend-owned directory can never be the file the shim writes.
shim_mktemp() {
    mktemp "$STATE_DIR/.state.XXXXXX"
}

# Mark the pending job failed with $1 as the reason the admin UI shows.
fail_job() {
    local tmp
    log "ERROR $1 - marking job failed"
    tmp=$(shim_mktemp)
    jq --arg err "$1" --arg now "$(date -u +%Y-%m-%dT%H:%M:%S)" \
       '. + {status: "failed", error: $err, finished_at: $now}' \
       "$STATE_FILE" > "$tmp" && install_state "$tmp"
}

# The version the running backend was built as, from the docker daemon's view
# of its container (the FH_VERSION label baked into the image) - never from the
# job file, which the backend writes. Prints nothing when unknown: no such
# container, or a dev build ("0.0.0-dev").
running_backend_version() {
    local v
    v=$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' \
            "${COMPOSE_PROJECT}-backend" 2>/dev/null || true)
    case "$v" in
        *[!0-9.v]*) v="" ;;
    esac
    if [ -n "$v" ] && printf '%s' "$v" | grep -Eqx 'v[0-9]+\.[0-9]+\.[0-9]+'; then
        printf '%s' "$v"
    fi
}

# 0 when release tag $1 is lower than release tag $2 (both vX.Y.Z).
version_lt() {
    local a=${1#v} b=${2#v} a1 a2 a3 b1 b2 b3
    IFS=. read -r a1 a2 a3 <<< "$a"
    IFS=. read -r b1 b2 b3 <<< "$b"
    if [ "$a1" -ne "$b1" ]; then [ "$a1" -lt "$b1" ]; return; fi
    if [ "$a2" -ne "$b2" ]; then [ "$a2" -lt "$b2" ]; return; fi
    [ "$a3" -lt "$b3" ]
}

# The private record: $1 file name under PRIVATE_DIR, $2 its JSON. Atomic and
# owner-only; the directory is root's and nothing else mounts it.
private_write() {
    local tmp
    tmp=$(mktemp "$PRIVATE_DIR/.tmp.XXXXXX") || return 1
    printf '%s\n' "$2" > "$tmp" && chmod 0600 "$tmp" && mv "$tmp" "$PRIVATE_DIR/$1"
}

# $1 file name under PRIVATE_DIR, $2 a jq path; prints "" when absent.
private_read_field() {
    jq -r "$2 // \"\"" "$PRIVATE_DIR/$1" 2>/dev/null || true
}

# Verify the signature of $1 (an image reference by tag) against the release
# workflow's identity. Sets VERIFIED_DIGEST to the manifest digest and returns
# 0; returns 1 (VERIFIED_DIGEST empty) when there is no acceptable signature.
# cosign runs as `cmd & wait $!` like every other blocking step (see on_signal):
# inside a command substitution a SIGTERM could not reach it.
verify_image() {
    local digest
    VERIFIED_DIGEST=""
    cosign verify --certificate-oidc-issuer "$OIDC_ISSUER" \
        --certificate-identity-regexp "$SIGNER_IDENTITY_RE" "$1" >"$VERIFY_OUT" 2>"$VERIFY_LOG" &
    if ! wait $!; then
        log "signature check of $1 failed: $(tail -n 1 "$VERIFY_LOG" 2>/dev/null | cut -c1-300)"
        return 1
    fi
    digest=$(jq -r '.[0].critical.image."docker-manifest-digest" // ""' "$VERIFY_OUT" 2>/dev/null || true)
    if ! printf '%s' "$digest" | grep -Eqx 'sha256:[0-9a-f]{64}'; then
        log "signature check of $1 produced no sha256 digest"
        return 1
    fi
    VERIFIED_DIGEST="$digest"
}

log "fileheron-updater-shim starting (poll=${POLL_INTERVAL_SEC}s, ghcr=$GHCR_OWNER, project=$COMPOSE_PROJECT, signatures=$REQUIRE_SIGNATURE)"
log "workspace host=$HOST_WORKSPACE state host=$HOST_STATE"
if [ "$REQUIRE_SIGNATURE" != "true" ]; then
    log "WARN image signature verification is OFF (SHIM_REQUIRE_SIGNATURE=$REQUIRE_SIGNATURE): every update trusts the registry account alone"
fi

# /private is a bind mount from docker-compose.yml. A shim recreated from an
# older compose file (a checkout the executor could not fast-forward) runs
# without it: rollbacks then come from the job file alone, as before.
PRIVATE_OK=0
if [ -d "$PRIVATE_DIR" ] && [ -w "$PRIVATE_DIR" ]; then
    chmod 0700 "$PRIVATE_DIR" 2>/dev/null || true
    PRIVATE_OK=1
else
    log "WARN $PRIVATE_DIR is not mounted - a Rollback is not bound to the recorded version until docker-compose.yml is current and the shim recreated"
fi

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
        fail_job "shim restarted mid-job"
    fi
fi

while true; do
    sleep "$POLL_INTERVAL_SEC" & wait $!
    heartbeat

    [ -f "$STATE_FILE" ] || continue

    status=$(jq -r '.status // ""' "$STATE_FILE" 2>/dev/null || echo "")

    case "$status" in
        pending)
            target_tag=$(jq -r '.target_tag // ""' "$STATE_FILE")
            job_id=$(jq -r '.id // ""' "$STATE_FILE")
            action=$(jq -r '.action // "update"' "$STATE_FILE")
            # Validate INSIDE the privileged boundary, not only at the API.
            #
            # `UpdateApplyRequest._validate_target_tag` already enforces this
            # shape, but that check runs in the backend - the very component
            # whose compromise this container's docker.sock would amplify. The
            # /state channel must therefore never be more permissive than the
            # API in front of it. The registry path comes from this shim's own
            # compose env and never from the job file, so a tag can only ever
            # select an image the vendor published; this keeps it to a release
            # tag rather than relying on `docker pull` happening to reject
            # whatever string arrives.
            # Two steps, and the first one is the point: `grep` is
            # LINE-oriented, so `grep -Eq '^...$'` exits 0 when ANY line
            # matches. A target_tag of "v1.2.3\nFOO=bar" therefore PASSED this
            # check - and the executor writes the tag into the host .env as
            # `FH_TAG=<tag>`, where an embedded newline becomes an extra env
            # line every compose service reads.
            #
            # The `case` rejects any character outside [0-9.v], which covers
            # newlines, spaces and every shell metacharacter at once; the grep
            # then confirms the SHAPE of what survived. The executor validates
            # again on its own side (see _TAG_RE in run.py) - this check runs
            # before a `docker pull` the executor's read happens after.
            case "$target_tag" in
                *[!0-9.v]*) target_tag="" ;;
            esac
            if [ -z "$target_tag" ] \
               || ! printf '%s' "$target_tag" | grep -Eqx 'v[0-9]+\.[0-9]+\.[0-9]+'; then
                fail_job "invalid target_tag"
                continue
            fi
            case "$action" in
                update|rollback) ;;
                *)
                    fail_job "invalid action"
                    continue
                    ;;
            esac

            # The two rules the backend's API also enforces, repeated HERE
            # because the API runs in the component this socket would amplify:
            # an update only ever moves forward (the version comes from the
            # daemon, so a forged job cannot lie about it), and a rollback goes
            # only where the shim itself saw the stack before the last update.
            running=$(running_backend_version)
            if [ -z "$running" ]; then
                log "WARN the running backend's version is unknown (no container, or a dev build); the downgrade check cannot run"
            fi
            if [ "$action" = "update" ] && [ -n "$running" ] && version_lt "$target_tag" "$running"; then
                fail_job "downgrade refused: $target_tag is older than the running $running; use Rollback to go back"
                continue
            fi
            bound=0
            if [ "$action" = "rollback" ]; then
                if [ "$PRIVATE_OK" = 1 ] && [ -f "$PRIVATE_DIR/rollback_target.json" ]; then
                    recorded=$(private_read_field rollback_target.json .tag)
                    if [ "$recorded" != "$target_tag" ]; then
                        fail_job "rollback refused: $target_tag is not the version the shim recorded before the last update ($recorded); if you moved versions by hand, remove data/updater-private/rollback_target.json on the host (as root) and retry"
                        continue
                    fi
                    bound=1
                else
                    log "WARN no recorded rollback target in $PRIVATE_DIR - this rollback's target comes from the job file alone"
                fi
            fi

            log "claiming job=$job_id action=$action target_tag=$target_tag running=${running:-unknown}"
            # Claim atomically: if another shim instance somehow exists,
            # only one flips status from pending → claiming first.
            tmp=$(shim_mktemp)
            jq --arg now "$(date -u +%Y-%m-%dT%H:%M:%S)" \
               '. + {status: "claiming", claimed_at: $now}' \
               "$STATE_FILE" > "$tmp" && install_state "$tmp"
            # Remember what was running, keyed by this job: the loop below
            # promotes it to the rollback record once the job ends healthy. Two
            # steps, because the executor recreates THIS shim as its last step,
            # so the shim that claimed a job rarely sees its `docker run`
            # return - the new shim's loop does.
            if [ "$PRIVATE_OK" = 1 ] && [ -n "$running" ]; then
                private_write claim.json "$(jq -cn --arg id "$job_id" --arg running "$running" '{id: $id, running: $running}')" \
                    || log "WARN could not record the claim in $PRIVATE_DIR"
            fi

            executor_image="ghcr.io/$GHCR_OWNER/fileheron-updater-executor:$target_tag"

            # Verify the signature first and pull + run BY THE VERIFIED DIGEST,
            # so what runs is what was verified even if the tag moves in between.
            # A rollback to a release that predates signing is allowed unsigned
            # only when its target is the one the shim recorded itself.
            if [ "$REQUIRE_SIGNATURE" != "true" ]; then
                log "WARN image signature verification is off (SHIM_REQUIRE_SIGNATURE=$REQUIRE_SIGNATURE); running $executor_image by tag"
            else
                log "verifying the signature of $executor_image"
                heartbeat "$STUCK_THRESHOLD_SEC"
                if verify_image "$executor_image"; then
                    executor_image="ghcr.io/$GHCR_OWNER/fileheron-updater-executor@$VERIFIED_DIGEST"
                    log "verified: $executor_image"
                elif [ "$action" = "rollback" ] && [ "$bound" = 1 ]; then
                    log "WARN $target_tag predates image signing; rolling back to it unsigned because it is the version the shim recorded"
                else
                    fail_job "no valid signature on $executor_image - it must be signed by the release workflow (SHIM_REQUIRE_SIGNATURE=false disables this check); nothing was changed"
                    continue
                fi
            fi

            # Pull the executor for this tag. If the registry is
            # unreachable or the tag doesn't exist, fail the job here
            # before spawning anything.
            log "pulling $executor_image"
            heartbeat "$STUCK_THRESHOLD_SEC"
            docker pull "$executor_image" &
            if ! wait $!; then
                fail_job "executor pull failed: $executor_image"
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
            #
            # The executor is root with the socket, so the container itself
            # keeps only what the work needs: the capabilities to write the
            # uid-1000 state dir and the operator's checkout (DAC_OVERRIDE,
            # DAC_READ_SEARCH, FOWNER), to hand backups to the checkout's owner
            # (CHOWN) and to run git as that owner (SETUID, SETGID); no new
            # privileges, a pids ceiling and a memory limit. Defence in depth
            # against a bug in the executor, not a boundary.
            heartbeat "$STUCK_THRESHOLD_SEC"
            docker run --rm \
                --name "$container_name" \
                --network "$NETWORK_NAME" \
                --security-opt no-new-privileges \
                --cap-drop ALL \
                --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add DAC_READ_SEARCH \
                --cap-add FOWNER --cap-add SETUID --cap-add SETGID \
                --pids-limit 256 \
                --memory "$EXECUTOR_MEM_LIMIT" \
                -v /var/run/docker.sock:/var/run/docker.sock \
                -v "$HOST_WORKSPACE:/workspace" \
                -v "$HOST_STATE:/state" \
                -e "COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT" \
                -e "COMPOSE_HOST_ROOT=$HOST_WORKSPACE" \
                -e "UPDATER_HOST_WORKSPACE=$HOST_WORKSPACE" \
                -e "UPDATER_HOST_STATE=$HOST_STATE" \
                -e "GHCR_OWNER=$GHCR_OWNER" \
                -e "EXECUTOR_REQUIRE_SIGNATURE=$REQUIRE_SIGNATURE" \
                -e "EXECUTOR_SIGNER_IDENTITY_RE=$SIGNER_IDENTITY_RE" \
                "$executor_image" &
            wait $! || exit_code=$?

            log "executor exited (code=$exit_code)"
            # The executor updates status itself (healthy/failed/etc.)
            # via the state file. We don't override what it wrote - it
            # has more context than we do about WHY it failed.
            # The only safety net: if the executor died without writing
            # a terminal status, mark it failed here.
            if [ -f "$STATE_FILE" ]; then
                final=$(jq -r '.status // ""' "$STATE_FILE")
                if [ "$final" != "healthy" ] && [ "$final" != "failed" ] && [ "$final" != "rolled_back" ]; then
                    fail_job "executor crashed (exit $exit_code) without writing status"
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
                    fail_job "stuck (no progress)"
                fi
            fi
            ;;

        healthy|failed|rolled_back|"")
            # Terminal. Settle the claim recorded for THIS job: a job that ended
            # healthy makes the version that was running before it the rollback
            # record; a failed or rolled-back job leaves the record alone. The
            # job id ties the two files together, and a job the backend never
            # claimed through this shim has no claim to settle.
            if [ "$PRIVATE_OK" = 1 ] && [ -n "$status" ] && [ -f "$PRIVATE_DIR/claim.json" ]; then
                claim_id=$(private_read_field claim.json .id)
                state_id=$(jq -r '.id // ""' "$STATE_FILE" 2>/dev/null || echo "")
                if [ -n "$claim_id" ] && [ "$claim_id" = "$state_id" ]; then
                    if [ "$status" = "healthy" ]; then
                        prev=$(private_read_field claim.json .running)
                        if private_write rollback_target.json "$(jq -cn --arg tag "$prev" '{tag: $tag}')"; then
                            log "recorded $prev as the only version a Rollback may return to"
                        else
                            log "WARN could not record the rollback target in $PRIVATE_DIR"
                        fi
                    fi
                    rm -f "$PRIVATE_DIR/claim.json"
                fi
            fi
            ;;

        *)
            log "WARN unknown status: $status"
            ;;
    esac
done
