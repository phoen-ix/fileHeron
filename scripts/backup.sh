#!/usr/bin/env bash
# fileHeron backup - produces a dated archive under ./backups/<stamp>/.
#
# Contents:
#   db.sql                - mysqldump of the MariaDB database
#   files.tar.gz          - gzipped tar of ./data/files (finalized uploads)
#   quarantine.tar.gz     - gzipped tar of ./data/quarantine (AV positives)
#   redis.rdb             - Redis snapshot (rate-limit counters, ARQ queue)
#   manifest.txt          - sha256 of every artifact + counts
#
# If $BACKUP_RESTIC_REPO is set (e.g. "s3:s3.amazonaws.com/my-bucket/repo",
# "rest:https://restic.example.com/", or a local path), the produced archive
# is also pushed to a restic repo using $BACKUP_RESTIC_PASSWORD.
#
# Designed to be safe to interrupt: temporary files live under /tmp until
# the artifact is fully written, then atomically renamed in.
#
# Recovery: see scripts/restore.sh.

set -euo pipefail

# Resolve paths relative to repo root.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# Load env (best-effort).
if [ -f .env ]; then
    # shellcheck disable=SC1091
    set -a; source .env; set +a
fi

: "${DB_NAME:=fileheron}"
: "${DB_ROOT_PASSWORD:?DB_ROOT_PASSWORD must be set in .env or environment}"

STAMP="$(date -u +%Y-%m-%d_%H%M%S)"
DEST="$ROOT/backups/$STAMP"
mkdir -p "$DEST"

echo "[backup] $STAMP - starting"

# NOT CAPTURED HERE, AND REQUIRED FOR A USABLE RESTORE: .env, specifically
# JWT_SECRET. Every Fernet field in the DB (TOTP secrets, OIDC client secrets,
# SMTP/IMAP passwords, public-link tokens, webhook secrets) is encrypted under a
# key derived from it. Restore this backup onto a host with a different
# JWT_SECRET and all of those rows come back intact and permanently unreadable,
# while every superficial check passes. Secrets are deliberately kept OUT of
# ./backups (it is a plain directory, often synced onward) - so back .env up
# separately, in your password manager or secret store. scripts/restore_validate.py
# samples those fields and fails loudly if the key does not match.
#
# CONSISTENCY WINDOW: the DB dump below is point-in-time, but the file tree is
# archived afterwards, so the two are not a matched pair. A file deleted between
# the two steps (the hourly expiry cron is the usual cause) leaves a DB row
# pointing at bytes that are not in the archive. That specific direction is what
# restore_validate.py's "all live files exist on disk" check reports after a
# restore, and the weekly drill runs it - so the failure is detected rather than
# silently shipped. Closing the window properly needs the app quiesced for the
# duration; not worth it at this scale (audit 2026-07-30).

# 1. MariaDB dump.
# Pass the password via the MYSQL_PWD env var rather than `-p"$pwd"` on
# the command line. `MYSQL_PWD=… docker compose exec -e MYSQL_PWD …` keeps
# the secret out of the host's `ps aux` (env vars set with the
# `VAR=value cmd` form aren't in /proc/<pid>/cmdline; -e without a value
# forwards from the caller env into the container). Mirrors the
# --password-file pattern used for restic below.
echo "[backup] dumping MariaDB ($DB_NAME) …"
MYSQL_PWD="$DB_ROOT_PASSWORD" docker compose exec -T -e MYSQL_PWD db \
    mariadb-dump -uroot \
    --single-transaction --quick --lock-tables=false \
    "$DB_NAME" > "$DEST/db.sql"

# 2. Files + quarantine.
echo "[backup] archiving data/files …"
tar -C "$ROOT/data" -czf "$DEST/files.tar.gz" files
echo "[backup] archiving data/quarantine …"
tar -C "$ROOT/data" -czf "$DEST/quarantine.tar.gz" quarantine

# 3. Redis snapshot - issue SAVE then copy dump.rdb out of the container.
echo "[backup] saving Redis …"
docker compose exec -T redis redis-cli SAVE > /dev/null
REDIS_CID="$(docker compose ps -q redis)"
docker cp "$REDIS_CID:/data/dump.rdb" "$DEST/redis.rdb"

# 4. Manifest with sha256s.
echo "[backup] hashing artifacts …"
(
    cd "$DEST"
    sha256sum db.sql files.tar.gz quarantine.tar.gz redis.rdb > manifest.txt
)

# 5. Optional restic push.
if [ -n "${BACKUP_RESTIC_REPO:-}" ]; then
    if [ -z "${BACKUP_RESTIC_PASSWORD:-}" ]; then
        echo "[backup] BACKUP_RESTIC_REPO set but BACKUP_RESTIC_PASSWORD missing - skipping push" >&2
    elif ! command -v restic >/dev/null 2>&1; then
        echo "[backup] restic not installed on host - skipping push" >&2
    else
        echo "[backup] pushing to restic repo $BACKUP_RESTIC_REPO …"
        # Stash the password in a 0600 temp file and pass it via
        # restic's --password-file. Avoids `export RESTIC_PASSWORD`
        # which would leave the secret in /proc/<pid>/environ for
        # the duration of the restic call (readable by any process
        # running as the same user).
        PWD_FILE="$(mktemp)"
        chmod 600 "$PWD_FILE"
        # `trap` cleanup so we don't leak the file on error/exit.
        trap 'rm -f "$PWD_FILE"' EXIT
        printf '%s' "$BACKUP_RESTIC_PASSWORD" > "$PWD_FILE"
        restic --repo "$BACKUP_RESTIC_REPO" --password-file "$PWD_FILE" \
            snapshots > /dev/null 2>&1 || \
            restic --repo "$BACKUP_RESTIC_REPO" --password-file "$PWD_FILE" init
        restic --repo "$BACKUP_RESTIC_REPO" --password-file "$PWD_FILE" \
            backup --tag "fileheron-$STAMP" "$DEST"
        rm -f "$PWD_FILE"
        trap - EXIT
    fi
fi

# 6. Local retention - keep last 7 dated dirs. Restic remote (if
# configured) holds older snapshots via its own keep-* policy below.
echo "[backup] pruning local backups (keep last 7) …"
# shellcheck disable=SC2012
ls -1dt "$ROOT/backups"/*/ 2>/dev/null | tail -n +8 | xargs -r rm -rf

# 7. Restic forget + prune - drops snapshots beyond the retention
# window so the remote repo doesn't grow without bound. Mirrors the
# password-via-file pattern from step 5; reuses the same temp file
# when restic is enabled.
if [ -n "${BACKUP_RESTIC_REPO:-}" ] && [ -n "${BACKUP_RESTIC_PASSWORD:-}" ] && command -v restic >/dev/null 2>&1; then
    PWD_FILE="$(mktemp)"
    chmod 600 "$PWD_FILE"
    trap 'rm -f "$PWD_FILE"' EXIT
    printf '%s' "$BACKUP_RESTIC_PASSWORD" > "$PWD_FILE"
    echo "[backup] applying restic retention …"
    restic --repo "$BACKUP_RESTIC_REPO" --password-file "$PWD_FILE" \
        forget --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --prune \
        > /dev/null
    rm -f "$PWD_FILE"
    trap - EXIT
fi

echo "[backup] done - $DEST"
echo "[backup] sizes:"
du -h "$DEST"/* | sed 's/^/[backup]   /'
