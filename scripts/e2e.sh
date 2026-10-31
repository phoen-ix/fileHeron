#!/usr/bin/env bash
# The e2e suite against a throwaway stack: build the prod images, start them
# under their own compose project, run Playwright (e2e/), tear it all down.
#
#   scripts/e2e.sh                      # every spec, then the docs tour as a render check
#   scripts/e2e.sh tests/user/x.spec.ts # only these Playwright args (no render check,
#                                       # unless E2E_DOCS_TOUR=1 - CI's shards use that)
#
# PORT_BASE (default 8080): the SPA on 127.0.0.1:PORT_BASE, the API on +3, the
# stand-in OIDC provider (e2e/oidc/) on +4.
# COMPOSE_PROJECT_NAME (default fileheron_e2e): never `fileheron`, which is
# what compose calls a checkout's live stack. ARTIFACTS: Playwright's report,
# results and the stack log on failure (default: a temp dir).
#
# Like the docs tour it bind-mounts this checkout's data/, so it refuses to
# start on top of a data/ that holds anything and empties what it wrote on the
# way out. Node + `npm ci` + `npx playwright install chromium` in e2e/ are the
# caller's.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ARTIFACTS="${ARTIFACTS:-$(mktemp -d)}"
mkdir -p "$ARTIFACTS"
ARTIFACTS="$(cd "$ARTIFACTS" && pwd)"
PORT_BASE="${PORT_BASE:-8080}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-fileheron_e2e}"

case "${COMPOSE_PROJECT_NAME,,}" in
  fileheron | phytotron | phytotron-dev | vib | keyb-dev)
    echo "e2e: refusing the live project name '$COMPOSE_PROJECT_NAME'" >&2
    exit 1 ;;
esac
if [ -e .env ]; then
  echo "e2e: this checkout has a .env (a local instance?); run from a clean clone" >&2
  exit 1
fi
if [ -n "$(find data -mindepth 2 ! -name .gitkeep -print -quit 2>/dev/null)" ]; then
  echo "e2e: data/ holds files (a local stack?) - refusing to start on top of it" >&2
  exit 1
fi

unset COMPOSE_HOST_ROOT
export APP_FRONTEND_PORT="$PORT_BASE" APP_BACKEND_PORT="$((PORT_BASE + 3))"
# The stand-in OIDC provider's sign-in form (e2e/oidc/idp.py).
export E2E_IDP_PORT="$((PORT_BASE + 4))"
# The passkey RP origin is APP_URL: it must be the address the browser uses.
export APP_URL="http://localhost:$PORT_BASE"
# Compose requires these (${VAR:?}); test-only values.
export DB_ROOT_PASSWORD=e2e-root-not-a-secret DB_PASSWORD=e2e-db-not-a-secret
export JWT_SECRET=e2e-jwt-secret-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
export TUS_HOOK_SECRET=e2e-tus-secret-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
# Image tags per project, so two checkouts' stacks never run each other's build.
export E2E_IMAGE_TAG="${COMPOSE_PROJECT_NAME,,}"

compose() {
  docker compose -f docker-compose.yml -f docker-compose.e2e.yml "$@"
}
teardown() {
  rc=$?
  [ "$rc" = 0 ] || compose logs --tail=300 >"$ARTIFACTS/stack.log" 2>&1 || true
  compose down -v --remove-orphans --rmi local || true
  docker run --rm -v "$ROOT/data":/d alpine sh -c \
    'for x in db redis files uploads quarantine updater; do [ -d /d/$x ] && find /d/$x -mindepth 1 ! -name .gitkeep -exec rm -rf {} +; done; chown -R 1000:1000 /d' || true
  exit "$rc"
}
trap teardown EXIT

mkdir -p data/files data/uploads data/quarantine data/updater
# The containers write as UID 1000.
docker run --rm -v "$ROOT/data":/d alpine chown -R 1000:1000 /d/files /d/uploads /d/quarantine /d/updater
# No clamav (AV_SKIP) and no updater-shim - unless E2E_FULL_STACK=1 (CI): this
# stack is the one place the capability policy (cap_drop ALL + a measured
# cap_add per service, docker-compose.yml) is exercised, so CI runs them too.
compose up -d --build --wait db redis backend worker tusd frontend oidc
[ "${E2E_FULL_STACK:-0}" = 1 ] && compose up -d clamav updater-shim

cd e2e
export E2E_BASE_URL="http://localhost:$PORT_BASE" E2E_BACKEND_CONTAINER="$COMPOSE_PROJECT_NAME-backend"
export PLAYWRIGHT_HTML_OUTPUT_DIR="$ARTIFACTS/playwright-report" PLAYWRIGHT_HTML_OPEN=never
npx playwright test --output "$ARTIFACTS/test-results" "$@"
# The docs tour LAST (a whole-suite run, or E2E_DOCS_TOUR=1): its seed changes
# settings the specs rely on. Its screenshots show the e2e fixtures - never
# commit them.
if [ "$#" -eq 0 ] || [ "${E2E_DOCS_TOUR:-0}" = 1 ]; then
  DOCS_SCREENSHOT_DIR="$ARTIFACTS/docs-tour" \
    npx playwright test -c playwright.docs.config.ts --output "$ARTIFACTS/docs-tour-results"
fi

# `up` returning 0 is no verdict on the capability policy: a redis that stays
# root, a clamav whose init dies, an nginx that cannot bind :80 would pass the
# specs. So every container must still be running and healthy at the end; a
# fresh clamav's ~150 MB signature download gets five minutes to settle.
for _ in $(seq 1 60); do
  bad="$(compose ps -a --format '{{.Service}} state={{.State}} health={{.Health}}' \
    | grep -vE 'state=running health=(healthy)?$' || true)"
  [ -z "$bad" ] && break
  grep -qv 'state=running ' <<<"$bad" && break # a stopped one will not recover
  sleep 5
done
if [ -n "$bad" ]; then
  echo "e2e: containers not running and healthy: $bad" >&2
  exit 1
fi
