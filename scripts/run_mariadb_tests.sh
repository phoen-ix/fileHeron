#!/usr/bin/env bash
# Run the three test files that need a REAL MariaDB, locally, in throwaway
# containers that clean themselves up.
#
#   scripts/run_mariadb_tests.sh              # all three files
#   scripts/run_mariadb_tests.sh tests/test_mariadb_row_locks.py
#
# WHY THIS EXISTS. tests/test_alembic_roundtrip.py, tests/test_mariadb_semantics.py
# and tests/test_mariadb_row_locks.py are gated on RUN_ALEMBIC_ROUNDTRIP and skip
# without a real database. CI runs them as a GitHub Actions `service:` container,
# which the runner disposes of with the VM. Locally there was no supported path
# at all - the docstrings said only "point DB_* at a throwaway MariaDB" - so
# every session invented one, and the invented one was
# `docker run -d --name … mariadb:11` with no --rm, torn down later with
# `docker rm -f` and no -v. mariadb:11 declares VOLUME /var/lib/mysql, so each
# cycle stranded a ~167 MB anonymous volume. Six of them accumulated in a single
# day on the reference host before anyone noticed, which is how a procedure that
# lives only in someone's head fails: it gets re-derived, and re-derived wrong.
#
# The teardown below is deliberately belt AND braces:
#   * `--rm` on `docker run -d` covers the normal exit, but does NOT fire if the
#     container is killed outright or the daemon restarts under it.
#   * `docker rm -f -v` in the trap covers those, and the `-v` is the only thing
#     that takes the anonymous datadir with it. `docker rm -f` alone - which is
#     what the hand-rolled recipe did - leaves the volume behind forever.
# Either alone leaks in a case the other catches. Keep both.
#
# Nothing here touches the live stack: own network, own container names, own
# database. That matters more than it sounds - docker-compose.e2e.yml carries a
# warning for the same reason, because compose defaults its project name to the
# directory and will happily recreate the production containers.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

PREFIX=fh-mdbtest
DB_CONTAINER="${PREFIX}-db"
NETWORK="${PREFIX}-net"

# Not secrets: this database exists for the length of one command and is
# reachable only from its own bridge network.
DB_NAME=fileheron_rt
DB_USER=rt
DB_PASSWORD=rt-not-a-secret
DB_ROOT_PASSWORD=root-not-a-secret

# Pinned to what the backend image and CI actually use, so a green run here
# means the same thing a green run there does.
MARIADB_IMAGE=mariadb:11
PYTHON_IMAGE=python:3.14-slim

DEFAULT_FILES=(
    tests/test_alembic_roundtrip.py
    tests/test_mariadb_semantics.py
    tests/test_mariadb_row_locks.py
)
if [ "$#" -gt 0 ]; then
    FILES=("$@")
else
    FILES=("${DEFAULT_FILES[@]}")
fi

log() { echo "[mariadb-tests] $*"; }

teardown() {
    docker rm -f -v "$DB_CONTAINER" >/dev/null 2>&1 || true
    docker network rm "$NETWORK" >/dev/null 2>&1 || true
}

# Run it once up front too: a previous run that was killed hard (Ctrl-C during
# `docker run`, a reboot) leaves both behind, and `docker network create` fails
# with "network already exists" rather than reusing it.
teardown
trap teardown EXIT

log "starting a throwaway $MARIADB_IMAGE ..."
docker network create "$NETWORK" >/dev/null
docker run -d --rm --name "$DB_CONTAINER" --network "$NETWORK" \
    -e MARIADB_ROOT_PASSWORD="$DB_ROOT_PASSWORD" \
    -e MARIADB_DATABASE="$DB_NAME" \
    -e MARIADB_USER="$DB_USER" \
    -e MARIADB_PASSWORD="$DB_PASSWORD" \
    "$MARIADB_IMAGE" >/dev/null

# mariadb's own readiness script, which is what CI's `service:` health-check
# uses. A connect loop is NOT a readiness gate: the server accepts connections
# before InnoDB has finished initialising, and the migrations are the first
# thing that runs. The restore drill was broken for exactly this reason (a
# `redis-cli PING` loop that exits 0 on an error reply) - see CLAUDE.md.
log "waiting for InnoDB to finish initialising ..."
ready=0
for _ in $(seq 1 60); do
    if docker exec "$DB_CONTAINER" healthcheck.sh --connect --innodb_initialized >/dev/null 2>&1; then
        ready=1; break
    fi
    sleep 2
done
if [ "$ready" != "1" ]; then
    echo "[mariadb-tests] FATAL: $MARIADB_IMAGE never became ready within 120s" >&2
    docker logs "$DB_CONTAINER" 2>&1 | tail -40 >&2
    exit 1
fi
log "database ready"

# -rs prints the skip reasons. If the gate ever stops being satisfied these
# files go quiet rather than red, which is the failure this whole script is
# about - so make the skips visible instead of trusting the exit code.
#
# PYTHONDONTWRITEBYTECODE: the checkout is bind-mounted rw and the container
# runs as root, so without it every run seeds root-owned __pycache__ into the
# working tree.
log "running: ${FILES[*]}"
docker run --rm --network "$NETWORK" \
    -v "$ROOT":/src -w /src/backend \
    -e PYTHONPATH=/src/backend \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -e RUN_ALEMBIC_ROUNDTRIP=1 \
    -e DB_HOST="$DB_CONTAINER" \
    -e DB_PORT=3306 \
    -e DB_NAME="$DB_NAME" \
    -e DB_USER="$DB_USER" \
    -e DB_PASSWORD="$DB_PASSWORD" \
    -e JWT_SECRET=local-mariadb-tests-jwt-secret-aaaaaaaaaa \
    -e TUS_HOOK_SECRET=local-mariadb-tests-tus-secret-aaaaaaaaaa \
    -e APP_URL=http://local.invalid \
    -e APP_NAME=fileHeron \
    "$PYTHON_IMAGE" sh -c '
        set -e
        # The LOCKED closure, exactly as CI installs it. Resolved ranges would
        # type- and run-check against whatever PyPI served this morning rather
        # than what the image ships.
        pip install -q --require-hashes -r requirements.lock
        pip install -q pytest pytest-asyncio aiosqlite
        python -m pytest -q -p no:cacheprovider -rs '"${FILES[*]}"'
    '

log "done - containers and volumes removed by the trap"
