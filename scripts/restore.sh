#!/usr/bin/env bash
# fileHeron restore - reverses scripts/backup.sh.
#
# Usage:
#   scripts/restore.sh ./backups/2026-05-15_021500
#
# What it does (with confirmations):
#   1. Verifies sha256s in manifest.txt
#   2. Stops the docker stack
#   3. Wipes ./data/{db,files,quarantine,redis} - DESTRUCTIVE
#   4. Restores DB via mariadb client (drops and re-imports DB_NAME)
#   5. Restores files.tar.gz + quarantine.tar.gz under ./data/
#   6. Restores redis.rdb into the redis container's volume (removing the AOF
#      first - see the step itself for why that is load-bearing)
#   7. Brings the stack back up
#
# This is irreversible. Read the prompts.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

if [ "${1:-}" = "" ]; then
    echo "Usage: $0 <backup-dir>" >&2
    exit 1
fi

BACKUP="$(cd "$1" && pwd)"

if [ ! -f "$BACKUP/manifest.txt" ]; then
    echo "FATAL: $BACKUP doesn't look like a fileHeron backup (no manifest.txt)" >&2
    exit 2
fi

if [ -f .env ]; then
    # shellcheck disable=SC1091
    set -a; source .env; set +a
fi
: "${DB_NAME:=fileheron}"
: "${DB_ROOT_PASSWORD:?DB_ROOT_PASSWORD must be set in .env or environment}"

echo "[restore] verifying manifest …"
( cd "$BACKUP" && sha256sum -c manifest.txt )

echo
echo "  WARNING: this will DELETE the current database, data/files, data/quarantine,"
echo "           and Redis state, then restore from $BACKUP. This cannot be undone."
echo
read -r -p "  Type 'restore' to proceed: " ANSWER
[ "$ANSWER" = "restore" ] || { echo "[restore] aborted"; exit 3; }

echo "[restore] stopping stack …"
docker compose down

echo "[restore] wiping local data …"
rm -rf data/files data/quarantine
mkdir -p data/files data/quarantine

echo "[restore] restoring file archives …"
tar -C data -xzf "$BACKUP/files.tar.gz"
tar -C data -xzf "$BACKUP/quarantine.tar.gz"

# The containers run as UID 1000; the dirs above were just recreated as whoever
# invoked this script. On any host where that is not uid 1000, the backend,
# worker and tusd cannot write after the restore and uploads fail immediately -
# at the worst possible moment. install.sh and restore_drill_e2e.sh both already
# do this; restore.sh was the outlier (audit 2026-07-30).
echo "[restore] fixing ownership for the containers (UID 1000) …"
docker run --rm -v "$ROOT/data":/d alpine chown -R 1000:1000 /d/files /d/quarantine

echo "[restore] restoring redis snapshot …"
# Redis runs with `--appendonly yes`, and a Redis 7 server started with AOF
# enabled IGNORES dump.rdb completely - if there is no AOF it creates an empty
# one ("Creating AOF base file ... on server start") rather than loading the
# RDB. Copying the snapshot into /data was therefore a no-op that reported
# success: the restored instance came back EMPTY, losing every rate-limit
# bucket and every queued ARQ job, and nothing said so (audit 2026-07-30,
# ops-4). Verified against redis:7-alpine, both with and without the AOF
# directory present.
#
# The sequence that works, and the reason for each step:
#   1. stop redis and remove BOTH the AOF directory and the live dump.rdb
#   2. copy the backup's RDB in
#   3. start redis with AOF OFF, so it actually loads the RDB
#   4. CONFIG SET appendonly yes - rebuilds the AOF from the loaded dataset
#   5. recreate the service normally; it now reads that AOF
docker compose up -d redis
sleep 2
REDIS_CID="$(docker compose ps -q redis)"
docker compose stop redis
docker run --rm -v "$ROOT/data/redis":/d alpine sh -c 'rm -rf /d/appendonlydir /d/dump.rdb'
docker cp "$BACKUP/redis.rdb" "$REDIS_CID:/data/dump.rdb"
docker run --rm -v "$ROOT/data/redis":/d alpine chown 999:999 /d/dump.rdb

echo "[restore] loading redis snapshot with AOF disabled …"
docker run -d --rm --name fileheron-redis-restore \
    -v "$ROOT/data/redis":/data redis:7-alpine \
    redis-server --appendonly no > /dev/null
sleep 3
REDIS_KEYS="$(docker exec fileheron-redis-restore redis-cli DBSIZE | tr -d '\r')"
echo "[restore] redis loaded ${REDIS_KEYS:-0} keys from the snapshot"
if [ "${REDIS_KEYS:-0}" = "0" ]; then
    echo "[restore] WARNING: the redis snapshot loaded 0 keys - check ${BACKUP}/redis.rdb" >&2
fi
# Rebuild the AOF from what was just loaded, so the normal (AOF-on) service
# start below reads the restored dataset instead of an empty log.
docker exec fileheron-redis-restore redis-cli CONFIG SET appendonly yes > /dev/null
sleep 2
docker exec fileheron-redis-restore redis-cli INFO persistence \
    | grep -q 'aof_last_bgrewrite_status:ok' \
    || echo "[restore] WARNING: redis AOF rewrite did not report ok" >&2
docker exec fileheron-redis-restore redis-cli SHUTDOWN NOSAVE > /dev/null 2>&1 || true
sleep 2
docker rm -f fileheron-redis-restore > /dev/null 2>&1 || true
docker compose up -d redis

echo "[restore] restoring database …"
docker compose up -d db
# Wait for db to be reachable. Pass the password via MYSQL_PWD (forwarded
# by `docker compose exec -e MYSQL_PWD`) instead of `-p"$pwd"` so the
# secret never lands in the host's `ps aux`.
export MYSQL_PWD="$DB_ROOT_PASSWORD"
for _ in $(seq 1 30); do
    if docker compose exec -T -e MYSQL_PWD db mariadb -uroot -e "SELECT 1" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
docker compose exec -T -e MYSQL_PWD db mariadb -uroot \
    -e "DROP DATABASE IF EXISTS \`$DB_NAME\`; CREATE DATABASE \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
docker compose exec -T -e MYSQL_PWD db mariadb -uroot "$DB_NAME" < "$BACKUP/db.sql"
unset MYSQL_PWD

echo "[restore] starting full stack …"
docker compose up -d

echo "[restore] done - verify by hitting /api/health and signing in."
