#!/usr/bin/env bash
# fileHeron end-to-end restore drill.
#
# Proves a backup actually restores - the gap the README flags ("the restore
# path has not been exercised end-to-end"). Unlike scripts/restore.sh (which
# overwrites the LIVE stack), this restores into a fully ISOLATED throwaway
# compose project and never touches production data:
#   - distinct COMPOSE_PROJECT_NAME (fileheron_drill) -> distinct containers
#   - distinct COMPOSE_HOST_ROOT (a temp workspace) -> distinct data dirs
#   - APP_BACKEND_PORT remapped off :8000 so it can't collide with the live API
#   - seeding disabled (empty ADMIN_BOOTSTRAP_*/TEST_ACCOUNT_*) so restored data
#     stays pristine for validation
# It then runs scripts/restore_validate.py inside the throwaway backend, records
# the success timestamp, and tears the throwaway project + workspace down.
#
# Steps: artifact check -> restore db/files/quarantine/redis into the throwaway
# stack -> `alembic upgrade head` (the image's entrypoint; exercises forward
# migration of an old backup) -> restore_validate.py.
#
# Safe to run from cron/systemd: alerts on non-zero exit, always tears down (even
# on failure), and only ever operates on the `fileheron_drill` project.
#
# Usage:
#   scripts/restore_drill_e2e.sh [<backup-dir>]   # defaults to the newest backup

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

PROJECT="fileheron_drill"
WORKSPACE=""

log() { echo "[drill] $*"; }
fail() { echo "[drill] FAIL: $*" >&2; exit 1; }

# --- pick the backup --------------------------------------------------------
if [ -n "${1:-}" ]; then
    BACKUP="$(cd "$1" && pwd)"
else
    BACKUP="$(find "$ROOT/backups" -maxdepth 1 -mindepth 1 -type d -name '20*' | sort | tail -1)"
    [ -n "$BACKUP" ] || fail "no backup found under ./backups"
fi
[ -f "$BACKUP/manifest.txt" ] || fail "$BACKUP is not a fileHeron backup (no manifest.txt)"

# --- refuse stale evidence --------------------------------------------------
#
# The selection above takes the newest backup BY NAME and asserted nothing
# about its age. So once backups stopped arriving - which is exactly what the
# tar self-destruct in backup.sh used to cause, silently - this drill happily
# re-verified the same old archive and refreshed LAST_SUCCESSFUL_DRILL every
# week. A green drill then meant "some old backup is still intact", not
# "backups are being taken", while README.md and CLAUDE.md both cite that file
# as proof the pipeline works. A drill that cannot go red when the pipeline
# stops is not evidence.
: "${DRILL_MAX_BACKUP_AGE_HOURS:=48}"
backup_age_hours() {
    local dir="$1" stamp epoch="" now
    stamp="$(basename "$dir")"
    # Directory names are `date -u +%Y-%m-%d_%H%M%S` - the same field the
    # selection sorts on, so trusting it here is consistent with trusting it
    # there. Fall back to mtime for a hand-passed directory that does not
    # follow the convention.
    if [[ "$stamp" =~ ^([0-9]{4}-[0-9]{2}-[0-9]{2})_([0-9]{2})([0-9]{2})([0-9]{2})$ ]]; then
        epoch="$(date -u -d \
            "${BASH_REMATCH[1]} ${BASH_REMATCH[2]}:${BASH_REMATCH[3]}:${BASH_REMATCH[4]}" \
            +%s 2>/dev/null || echo "")"
    fi
    [ -n "$epoch" ] || epoch="$(stat -c %Y "$dir")"
    now="$(date -u +%s)"
    echo $(( (now - epoch) / 3600 ))
}

AGE_HOURS="$(backup_age_hours "$BACKUP")"
if [ "$AGE_HOURS" -gt "$DRILL_MAX_BACKUP_AGE_HOURS" ]; then
    if [ -n "${1:-}" ]; then
        log "WARNING: $BACKUP is ${AGE_HOURS}h old (limit ${DRILL_MAX_BACKUP_AGE_HOURS}h);" \
            "drilling it anyway because it was named explicitly"
    else
        fail "newest backup is ${AGE_HOURS}h old (limit ${DRILL_MAX_BACKUP_AGE_HOURS}h)" \
             "- backups have stopped arriving. Fix the backup job first; a drill" \
             "against a stale archive proves nothing about the pipeline."
    fi
fi

log "drilling backup: $BACKUP (${AGE_HOURS}h old)"

# --- 0. cheap artifact integrity check first --------------------------------
#
# These checks used to live in scripts/restore_drill.sh, which the v1.56.0
# "remove dead code and stale scaffolding" pass deleted while this script still
# called it. Under `set -e` with `|| fail`, THIS DRILL THEN ABORTED AT STEP 0 -
# every run, from v1.56.0 (2026-07-03) until the 2026-07-30 audit found it. The
# weekly restore drill was reported as proven and scheduled while it had not
# executed once. Inlined here so there is no separate file to mistake for dead
# code again.
log "verifying artifacts ..."
artifact_fail=0
artifact_check() {
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then
        log "  PASS: $name"
    else
        echo "[drill]   FAIL: $name" >&2
        artifact_fail=$((artifact_fail + 1))
    fi
}

artifact_check "manifest sha256s match" \
    bash -c "cd '$BACKUP' && sha256sum -c manifest.txt"
artifact_check "files.tar.gz lists cleanly" tar -tzf "$BACKUP/files.tar.gz"
artifact_check "quarantine.tar.gz lists cleanly" tar -tzf "$BACKUP/quarantine.tar.gz"
artifact_check "db.sql has a SQL header" \
    bash -c "head -n 5 '$BACKUP/db.sql' | grep -qE '(MariaDB|MySQL|sqlite|^-- )'"
artifact_check "redis.rdb has the REDIS magic header" \
    bash -c "head -c 9 '$BACKUP/redis.rdb' | grep -q '^REDIS'"

# A dump that parses but declares almost nothing is the dangerous case: it
# passes every header check and restores an empty instance.
create_count="$(grep -c '^CREATE TABLE' "$BACKUP/db.sql" 2>/dev/null || echo 0)"
log "  info: db.sql declares $create_count tables"
if [ "$create_count" -lt 5 ]; then
    echo "[drill]   FAIL: db.sql declares only $create_count tables (expected dozens)" >&2
    artifact_fail=$((artifact_fail + 1))
else
    log "  PASS: db.sql table count is plausible"
fi

[ "$artifact_fail" -eq 0 ] || fail "$artifact_fail artifact check(s) failed"

# --- load secrets/config from .env (reused; the stack is isolated) ----------
if [ -f .env ]; then set -a; . ./.env; set +a; fi
: "${DB_NAME:=fileheron}"
: "${DB_ROOT_PASSWORD:?DB_ROOT_PASSWORD must be set (.env or env)}"
: "${FH_TAG:=latest}"

# --- isolated environment for every compose call ----------------------------
WORKSPACE="$(mktemp -d "${TMPDIR:-/tmp}/fh-drill.XXXXXX")"
export COMPOSE_PROJECT_NAME="$PROJECT"
export COMPOSE_HOST_ROOT="$WORKSPACE"
export APP_BACKEND_PORT=18000          # off the live :8000
export AV_SKIP=true
export ENVIRONMENT=development          # AV_SKIP is refused under production
# Disable bootstrap + dev seeding so the restored data is what we validate.
export ADMIN_BOOTSTRAP_EMAIL="" ADMIN_BOOTSTRAP_PASSWORD=""
export TEST_ACCOUNT_EMAIL="" TEST_ACCOUNT_PASSWORD="" TEST_ACCOUNT_DISPLAY_NAME=""

dc() { docker compose -f "$ROOT/docker-compose.yml" "$@"; }

cleanup() {
    log "tearing down throwaway project ..."
    dc down -v --remove-orphans >/dev/null 2>&1 || true
    [ -n "$WORKSPACE" ] && rm -rf "$WORKSPACE" 2>/dev/null || true
    # data dirs may be written root-owned by the containers; force-remove.
    [ -n "$WORKSPACE" ] && [ -d "$WORKSPACE" ] && \
        docker run --rm -v "$(dirname "$WORKSPACE")":/w alpine rm -rf "/w/$(basename "$WORKSPACE")" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# --- prepare the throwaway workspace ----------------------------------------
log "preparing isolated workspace at $WORKSPACE ..."
mkdir -p "$WORKSPACE/data/db" "$WORKSPACE/data/redis" "$WORKSPACE/data/files" \
         "$WORKSPACE/data/quarantine" "$WORKSPACE/data/uploads" "$WORKSPACE/data/updater" \
         "$WORKSPACE/docker/mariadb"
# init.sh, not init.sql: the .sql was renamed away on 2026-07-30 (6629aa7) and
# this line kept copying it, so `set -e` killed the drill on the next Sunday
# tick and every one after. The unit failed silently and
# backups/LAST_SUCCESSFUL_DRILL kept showing an old timestamp - the same
# "the drill has been broken for months" finding as last time (audit #2).
cp "$ROOT/docker/mariadb/init.sh" "$WORKSPACE/docker/mariadb/init.sh"
# The UID-1000 containers must own the bind-mount dirs (see the data-dir gotcha).
docker run --rm -v "$WORKSPACE/data":/d alpine chown -R 1000:1000 /d >/dev/null

log "extracting file archives ..."
tar -C "$WORKSPACE/data" -xzf "$BACKUP/files.tar.gz"
tar -C "$WORKSPACE/data" -xzf "$BACKUP/quarantine.tar.gz"
docker run --rm -v "$WORKSPACE/data":/d alpine chown -R 1000:1000 /d/files /d/quarantine >/dev/null

# --- bring up db + redis, restore into them ---------------------------------
log "starting throwaway db + redis ..."
dc up -d db redis
export MYSQL_PWD="$DB_ROOT_PASSWORD"
for _ in $(seq 1 60); do
    dc exec -T -e MYSQL_PWD db mariadb -uroot -e "SELECT 1" >/dev/null 2>&1 && break
    sleep 2
done
dc exec -T -e MYSQL_PWD db mariadb -uroot -e "SELECT 1" >/dev/null 2>&1 || fail "throwaway db never came up"

log "restoring database ..."
dc exec -T -e MYSQL_PWD db mariadb -uroot \
    -e "DROP DATABASE IF EXISTS \`$DB_NAME\`; CREATE DATABASE \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
dc exec -T -e MYSQL_PWD db mariadb -uroot "$DB_NAME" < "$BACKUP/db.sql"
unset MYSQL_PWD

log "restoring redis snapshot ..."
REDIS_CID="$(dc ps -q redis)"
dc stop redis >/dev/null
docker cp "$BACKUP/redis.rdb" "$REDIS_CID:/data/dump.rdb"
dc up -d redis

# --- bring up backend: entrypoint runs `alembic upgrade head` on the restored
#     schema (exercises forward-migration of an old backup), then serves ------
log "starting throwaway backend (migrates restored schema) ..."
dc up -d backend
for _ in $(seq 1 60); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' "${PROJECT}-backend" 2>/dev/null)" = "healthy" ] && break
    sleep 3
done
[ "$(docker inspect -f '{{.State.Health.Status}}' "${PROJECT}-backend" 2>/dev/null)" = "healthy" ] \
    || fail "throwaway backend never became healthy (migration or boot failure)"

# --- validate ---------------------------------------------------------------
log "running restore_validate.py inside the throwaway backend ..."
# The repo-root scripts/ isn't baked into the backend image (only backend/scripts
# is). Copy the validator in at /app/scripts so its backend-root + alembic.ini
# detection resolves to /app.
docker cp "$ROOT/scripts/restore_validate.py" "${PROJECT}-backend:/app/scripts/restore_validate.py"
if dc exec -T backend python scripts/restore_validate.py; then
    STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "$STAMP  $BACKUP" > "$ROOT/backups/LAST_SUCCESSFUL_DRILL"
    log "PASS - recorded $STAMP in backups/LAST_SUCCESSFUL_DRILL"
    exit 0
else
    fail "restore_validate reported failures"
fi
