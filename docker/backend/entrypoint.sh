#!/usr/bin/env bash
# fileHeron backend entrypoint:
#   1. Wait for the database (compose healthcheck handles dependency, but
#      include a short retry loop in case of races).
#   2. Run Alembic migrations.
#   3. Bootstrap admin if env says so.
#   4. Seed dev account if env says so AND ENVIRONMENT != production.
#   5. exec the CMD (uvicorn).

set -euo pipefail

echo "[entrypoint] waiting for DB at ${DB_HOST:-db}:${DB_PORT:-3306} ..."
# A real login, closed cleanly: a bare TCP connect-and-close made MariaDB log an
# "Aborted connection" warning on every container start (scripts/wait_for_db.py).
if python -m scripts.wait_for_db 30; then
    echo "[entrypoint] DB is reachable"
else
    echo "[entrypoint] FATAL: DB never became reachable" >&2
    exit 1
fi

echo "[entrypoint] running alembic upgrade head ..."
alembic upgrade head

echo "[entrypoint] running admin bootstrap (idempotent) ..."
python -m scripts.create_admin || \
    echo "[entrypoint] WARN: admin bootstrap failed (continuing)" >&2

if [ "${ENVIRONMENT:-development}" != "production" ] && [ -n "${TEST_ACCOUNT_EMAIL:-}" ]; then
    echo "[entrypoint] seeding dev test account (idempotent) ..."
    python -m scripts.seed_dev || \
        echo "[entrypoint] WARN: dev seed failed (continuing)" >&2
fi

echo "[entrypoint] starting: $*"
exec "$@"
