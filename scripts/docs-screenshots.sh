#!/usr/bin/env bash
# Regenerate the docs screenshots: build the prod images, start a throwaway
# stack under its own compose project, run the docs tour (e2e/docs/) against
# it, tear everything down again.
#
#   scripts/docs-screenshots.sh [outdir]      # default: docs/screenshots
#
# PORT_BASE (default 8080): the SPA on 127.0.0.1:PORT_BASE, the API on +3.
# COMPOSE_PROJECT_NAME (default fileheron_docs): never `fileheron`, which is
# what compose calls a checkout's live stack. ARTIFACTS: where Playwright's
# test results go (default: a temp dir).
#
# Like the e2e stack it bind-mounts this checkout's data/, so it refuses to
# start on top of a data/ that holds anything (a local instance) and empties
# what it wrote on the way out. Node + `npm ci` in e2e/ are the caller's.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/docs/screenshots}"
OUT="$(mkdir -p "$OUT" && cd "$OUT" && pwd)"
cd "$ROOT"
ARTIFACTS="${ARTIFACTS:-$(mktemp -d)}"
mkdir -p "$ARTIFACTS"
PORT_BASE="${PORT_BASE:-8080}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-fileheron_docs}"

case "${COMPOSE_PROJECT_NAME,,}" in
  fileheron | phytotron | phytotron-dev | vib | keyb-dev)
    echo "docs-screenshots: refusing the live project name '$COMPOSE_PROJECT_NAME'" >&2
    exit 1 ;;
esac
if [ -e .env ]; then
  echo "docs-screenshots: this checkout has a .env (a local instance?); run from a clean clone" >&2
  exit 1
fi
if [ -n "$(find data -mindepth 2 ! -name .gitkeep -print -quit 2>/dev/null)" ]; then
  echo "docs-screenshots: data/ holds files (a local stack?) - refusing to start on top of it" >&2
  exit 1
fi

# Never a live root: the mounts must be this checkout's data/.
unset COMPOSE_HOST_ROOT
export APP_FRONTEND_PORT="$PORT_BASE" APP_BACKEND_PORT="$((PORT_BASE + 3))"
# Shown on the General settings page; the demo domain, whatever the port.
export APP_URL=https://files.heron-demo.example
# Compose requires these (${VAR:?}); test-only values.
export DB_ROOT_PASSWORD=docs-root-not-a-secret DB_PASSWORD=docs-db-not-a-secret
export JWT_SECRET=docs-jwt-secret-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
export TUS_HOOK_SECRET=docs-tus-secret-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
# The admin overview shows the version; the newest release tag, not 0.0.0-dev.
DOCS_FH_VERSION="$(git describe --tags --abbrev=0 --match 'v[0-9]*.[0-9]*.[0-9]*' 2>/dev/null || echo 0.0.0-dev)"
export DOCS_FH_VERSION

compose() {
  docker compose -f docker-compose.yml -f docker-compose.e2e.yml -f docker-compose.docs.yml "$@"
}
teardown() {
  rc=$?
  [ "$rc" = 0 ] || compose logs --tail=200 >"$ARTIFACTS/stack.log" 2>&1 || true
  compose down -v --remove-orphans --rmi local || true
  docker run --rm -v "$ROOT/data":/d alpine sh -c \
    'for x in db redis files uploads quarantine updater; do [ -d /d/$x ] && find /d/$x -mindepth 1 ! -name .gitkeep -exec rm -rf {} +; done; chown -R 1000:1000 /d' || true
  exit "$rc"
}
trap teardown EXIT

mkdir -p data/files data/uploads data/quarantine data/updater
# The containers write as UID 1000.
docker run --rm -v "$ROOT/data":/d alpine chown -R 1000:1000 /d/files /d/uploads /d/quarantine /d/updater
# No clamav (AV_SKIP) and no updater-shim.
compose up -d --build --wait db redis backend worker tusd frontend

cd e2e
E2E_BASE_URL="http://localhost:$PORT_BASE" \
  DOCS_SCREENSHOT_DIR="$OUT" \
  DOCS_ADMIN_EMAIL=anna.berger@heron-demo.example \
  DOCS_ADMIN_PASSWORD='AdminPass123!' \
  DOCS_DB_CONTAINER="$COMPOSE_PROJECT_NAME-db" \
  E2E_BACKEND_CONTAINER="$COMPOSE_PROJECT_NAME-backend" \
  npx playwright test -c playwright.docs.config.ts --output "$ARTIFACTS/test-results"
echo "docs-screenshots: $(find "$OUT" -name '*.png' | wc -l) PNGs in $OUT"
