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
# Safe to interrupt: artifacts are staged in ./backups/.partial-<stamp>/ and the
# directory is renamed to ./backups/<stamp>/ only after manifest.txt is written.
# An interrupted run therefore leaves no directory that looks complete, and the
# retention sweep only ever counts (and deletes) directories that have a
# manifest. The header previously claimed staging happened under /tmp; it did
# not - artifacts were written straight into the final directory.
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
FINAL="$ROOT/backups/$STAMP"
# Stage into a sibling `.partial` directory and rename only once the manifest is
# written. Artifacts used to be written straight into the final directory, so an
# interrupted run (the shipped systemd unit has TimeoutStartSec=1800, and SIGTERM
# is silent) left a half-written directory that looks exactly like a good backup
# to the retention sweep below (audit 2026-07-30). The rename is atomic within
# the same filesystem.
DEST="$ROOT/backups/.partial-$STAMP"
rm -rf "$DEST"
mkdir -p "$DEST"
# Never leave a partial behind, however we exit.
trap 'rm -rf "$DEST"' EXIT

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
#
# tar exits 1 - not 0 - when a file it is walking disappears underneath it
# ("File removed before we read it"). Under `set -e` that aborted the run and
# fired the EXIT trap above, which deletes the staging directory INCLUDING the
# already-complete db.sql: one routine deletion cost the whole night's backup,
# and the only trace was a `failed` unit. The deleter is ordinary - expire_files
# is an hourly INTERVAL cron so its firing time drifts through the backup
# window, and reclaim_orphaned_files runs 02:51 in the SITE timezone while the
# timer fires 03:00 in the HOST's, so the nine-minute gap is not a separation at
# all.
#
# Exit 1 is tar's warning class and the archive it produces is complete and
# extractable (measured: GNU tar 1.35, `tar -tzf` clean). Exit >=2 is a real
# error and must stay fatal. Note `--warning=no-file-removed` silences the
# message but does NOT change the exit status, so it is not a fix.
archive_tree() {
    local tree="$1" out="$2" rc=0

    echo "[backup] archiving data/$tree …"
    set +e
    tar -C "$ROOT/data" -czf "$out" "$tree"
    rc=$?
    set -e

    if [ "$rc" -eq 1 ]; then
        echo "[backup] WARNING: data/$tree changed while it was being archived;" \
             "the archive is complete but is not a point-in-time image"
        echo "$tree: tar exit 1 - files changed or were removed during archiving" \
            >> "$DEST/warnings.txt"
    elif [ "$rc" -ne 0 ]; then
        echo "[backup] FATAL: tar failed on data/$tree (exit $rc)" >&2
        exit "$rc"
    fi
}

archive_tree files "$DEST/files.tar.gz"
archive_tree quarantine "$DEST/quarantine.tar.gz"

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

# 4b. Promote the staged directory. Everything above succeeded and manifest.txt
# exists, so this is now a complete backup; the rename is what makes it visible
# to the retention sweep and to the restore drill.
mv "$DEST" "$FINAL"
trap - EXIT
DEST="$FINAL"

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
# Only COMPLETE backups (those with a manifest.txt) count toward the keep-7
# window, and only they are eligible for deletion. Pruning purely by directory
# mtime meant seven aborted runs in a row evicted the last good backup - the
# retention policy actively destroying the thing it exists to preserve.
#
# Ordering is by NAME, not mtime: STAMP is `%Y-%m-%d_%H%M%S`, so a reverse
# lexical sort is exactly chronological, and unlike mtime it cannot be
# rewritten by a sync tool, a restore, or a `touch`. This also drops the
# `ls`-parsing that shellcheck flagged (SC2045) once it was finally run.
#
# `if` rather than a trailing `&&`: under `set -e` + `pipefail` the loop adopts
# the exit status of its last command, so a final iteration whose test is false
# fails the whole pipeline and aborts the script - after the backup is already
# written, one line short of reporting success. A manifest-less directory that
# sorts last (`pre-v2.7.2-…`, since `p` > `2`) did exactly that.
for d in "$ROOT"/backups/*/; do
    if [ -f "$d/manifest.txt" ]; then printf '%s\n' "$d"; fi
done | sort -r | tail -n +8 | xargs -r rm -rf

# Sweep abandoned stages from earlier interrupted runs.
find "$ROOT/backups" -maxdepth 1 -type d -name '.partial-*' -mmin +180 -exec rm -rf {} + 2>/dev/null || true

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
