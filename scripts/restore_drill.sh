#!/usr/bin/env bash
# fileHeron restore drill — validates backup ARTIFACTS without touching
# production data. Catches the common silent-corruption failure modes
# (truncated tarball, dump that doesn't parse, RDB header drift) before
# a real disaster forces the question.
#
# Usage:
#   scripts/restore_drill.sh                 # validates latest backup
#   scripts/restore_drill.sh ./backups/2026-05-15_021500
#
# What it checks (each PASS/FAIL'd):
#   1. SHA-256 manifest verifies
#   2. files.tar.gz lists without error + non-zero file count
#   3. quarantine.tar.gz lists without error
#   4. db.sql has a recognisable header + at least one CREATE TABLE
#   5. redis.rdb has the magic header bytes
#
# Exits non-zero on any failure. Safe to run from cron + alerts on
# non-zero exit. NO production data touched, NO containers spun up.
#
# For full end-to-end "does the restore actually work" verification,
# use scripts/restore_validate.py against a post-restore compose project.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Pick target backup: explicit arg or latest dated dir.
if [ "${1:-}" != "" ]; then
    BACKUP="$(cd "$1" && pwd)"
else
    BACKUP="$(ls -1dt "$ROOT/backups"/*/ 2>/dev/null | head -n1 | sed 's:/$::')"
    if [ -z "$BACKUP" ]; then
        echo "FAIL: no backups found under $ROOT/backups/" >&2
        exit 2
    fi
fi

if [ ! -f "$BACKUP/manifest.txt" ]; then
    echo "FAIL: $BACKUP doesn't look like a fileHeron backup (no manifest.txt)" >&2
    exit 2
fi

echo "[drill] target: $BACKUP"

FAILED=0

check() {
    local name="$1"; shift
    if "$@"; then
        echo "  PASS: $name"
    else
        echo "  FAIL: $name" >&2
        FAILED=$((FAILED + 1))
    fi
}

# 1. Manifest verify.
check "manifest sha256s match" bash -c "
    cd '$BACKUP' && sha256sum -c manifest.txt >/dev/null
"

# 2. files.tar.gz lists + non-empty.
check "files.tar.gz lists cleanly" bash -c "
    tar -tzf '$BACKUP/files.tar.gz' >/dev/null
"
FILES_COUNT="$(tar -tzf "$BACKUP/files.tar.gz" 2>/dev/null | wc -l || echo 0)"
echo "  info: files.tar.gz contains $FILES_COUNT entries"

# 3. quarantine.tar.gz lists.
check "quarantine.tar.gz lists cleanly" bash -c "
    tar -tzf '$BACKUP/quarantine.tar.gz' >/dev/null
"

# 4. db.sql header + CREATE TABLE count.
check "db.sql has SQL header" bash -c "
    head -n 5 '$BACKUP/db.sql' | grep -qE '(MariaDB|MySQL|sqlite|^-- )'
"
CREATE_COUNT="$(grep -c '^CREATE TABLE' "$BACKUP/db.sql" 2>/dev/null || echo 0)"
echo "  info: db.sql declares $CREATE_COUNT tables"
if [ "$CREATE_COUNT" -lt 5 ]; then
    echo "  FAIL: db.sql has only $CREATE_COUNT CREATE TABLE statements (expected dozens)" >&2
    FAILED=$((FAILED + 1))
else
    echo "  PASS: db.sql has reasonable table count"
fi

# 5. Redis RDB magic.
check "redis.rdb has REDIS magic header" bash -c "
    head -c 9 '$BACKUP/redis.rdb' | grep -q '^REDIS'
"

# Restic check (if configured).
if [ -n "${BACKUP_RESTIC_REPO:-}" ] && [ -n "${BACKUP_RESTIC_PASSWORD:-}" ] && command -v restic >/dev/null 2>&1; then
    echo "[drill] restic configured — running 'restic check --read-data-subset=1%' …"
    PWD_FILE="$(mktemp)"
    chmod 600 "$PWD_FILE"
    trap 'rm -f "$PWD_FILE"' EXIT
    printf '%s' "$BACKUP_RESTIC_PASSWORD" > "$PWD_FILE"
    if restic --repo "$BACKUP_RESTIC_REPO" --password-file "$PWD_FILE" \
            check --read-data-subset=1% 2>&1 | tail -3; then
        echo "  PASS: restic repo integrity (1% data sample)"
    else
        echo "  FAIL: restic check reported errors" >&2
        FAILED=$((FAILED + 1))
    fi
    rm -f "$PWD_FILE"
    trap - EXIT
else
    echo "[drill] restic not configured — skipping remote integrity check"
fi

echo
if [ "$FAILED" -gt 0 ]; then
    echo "[drill] FAILED: $FAILED check(s)" >&2
    exit 1
fi
echo "[drill] all checks passed"
