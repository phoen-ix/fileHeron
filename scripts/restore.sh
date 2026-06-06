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
#   6. Restores redis.rdb into the redis container's volume
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

echo "[restore] restoring redis snapshot …"
# Wipe the persisted RDB; bring redis up alone so the volume is mounted; copy in.
docker compose up -d redis
sleep 2
REDIS_CID="$(docker compose ps -q redis)"
docker compose exec -T redis redis-cli FLUSHALL > /dev/null || true
docker compose stop redis
docker cp "$BACKUP/redis.rdb" "$REDIS_CID:/data/dump.rdb"
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
