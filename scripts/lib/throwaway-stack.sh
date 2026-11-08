# shellcheck shell=bash
# Shared by scripts/e2e.sh and scripts/docs-screenshots.sh - sourced, never run.
# Both start a throwaway stack on THIS checkout's data/ and tear it down again;
# they carried these guards and the teardown line for line (audit 2026-11-08).
# The caller defines `compose` (its overlay set) and ARTIFACTS, and has already
# exported COMPOSE_PROJECT_NAME and cd'd to $ROOT.

# Refuse anything that could be a live stack: a project name compose would
# share with a checkout's production stack, a checkout with a .env, or a data/
# that already holds files. $1 is the log prefix.
throwaway_stack_guards() {
  local who="$1"
  case "${COMPOSE_PROJECT_NAME,,}" in
    *_e2e | *_docs) ;;
    *)
      echo "$who: refusing the project name '$COMPOSE_PROJECT_NAME' (must end in _e2e or _docs)" >&2
      exit 1 ;;
  esac
  if [ -e .env ]; then
    echo "$who: this checkout has a .env (a local instance?); run from a clean clone" >&2
    exit 1
  fi
  if [ -n "$(find data -mindepth 2 ! -name .gitkeep -print -quit 2>/dev/null)" ]; then
    echo "$who: data/ holds files (a local stack?) - refusing to start on top of it" >&2
    exit 1
  fi
  # Never a live root: the mounts must be this checkout's data/.
  unset COMPOSE_HOST_ROOT
}

# The bind-mount dirs, owned by the UID the containers write as.
throwaway_prepare_data() {
  mkdir -p data/files data/uploads data/quarantine data/updater
  docker run --rm -v "$ROOT/data":/d alpine:3.24.4 chown -R 1000:1000 /d/files /d/uploads /d/quarantine /d/updater
}

# Trap target: keep the stack log on failure, take the stack and its volumes
# down, empty what it wrote to data/. $STACK_LOG_TAIL lines of log (default 300).
throwaway_teardown() {
  local rc=$?
  [ "$rc" = 0 ] || compose logs --tail="${STACK_LOG_TAIL:-300}" >"$ARTIFACTS/stack.log" 2>&1 || true
  compose down -v --remove-orphans --rmi local || true
  docker run --rm -v "$ROOT/data":/d alpine:3.24.4 sh -c \
    'for x in db redis files uploads quarantine updater; do [ -d /d/$x ] && find /d/$x -mindepth 1 ! -name .gitkeep -exec rm -rf {} +; done; chown -R 1000:1000 /d' || true
  exit "$rc"
}
