#!/usr/bin/env bash
# fileHeron installer — clones (or updates) the repo, generates random
# secrets, writes .env, and brings up the compose stack.
#
# Usage:
#   ./install.sh [--url=https://files.example.com] [--tag=v1.0.0] [--dir=/opt/fileHeron]
#
# After the script finishes, visit https://<your-url>/setup to create
# your first admin account through the web wizard. No more shell after
# install.
set -euo pipefail

APP_URL=""
FH_TAG="latest"
INSTALL_DIR="/opt/fileHeron"
REPO_URL="https://github.com/phoen-ix/fileHeron.git"

for arg in "$@"; do
    case "$arg" in
        --url=*)   APP_URL="${arg#*=}" ;;
        --tag=*)   FH_TAG="${arg#*=}" ;;
        --dir=*)   INSTALL_DIR="${arg#*=}" ;;
        --help|-h)
            cat <<EOF
fileHeron installer

  --url=URL    Public URL the app will be reached at (https://files.example.com).
               If not given, prompts interactively.
  --tag=TAG    GHCR image tag to deploy. Default: latest. Use v1.0.0 to pin.
  --dir=DIR    Install directory. Default: /opt/fileHeron.

After install, visit https://<your-url>/setup for the admin bootstrap wizard.
EOF
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

# ---- preflight -------------------------------------------------------

if ! command -v docker >/dev/null 2>&1; then
    echo "FATAL: docker is not installed. Install Docker Engine first:" >&2
    echo "  https://docs.docker.com/engine/install/" >&2
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "FATAL: docker compose plugin is missing. Install it:" >&2
    echo "  https://docs.docker.com/compose/install/linux/#install-using-the-repository" >&2
    exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
    echo "FATAL: openssl is required to generate secrets." >&2
    exit 1
fi

if ! command -v git >/dev/null 2>&1; then
    echo "FATAL: git is required to clone the repository." >&2
    exit 1
fi

# ---- collect APP_URL -------------------------------------------------

if [ -z "$APP_URL" ]; then
    read -rp "Public URL for fileHeron (e.g. https://files.example.com): " APP_URL
fi

if [ -z "$APP_URL" ]; then
    echo "FATAL: APP_URL is required (--url=... or interactive)." >&2
    exit 1
fi

APP_URL="${APP_URL%/}"

# ---- clone / update --------------------------------------------------

if [ -d "$INSTALL_DIR/.git" ]; then
    echo "[install] $INSTALL_DIR already exists — pulling latest"
    git -C "$INSTALL_DIR" fetch --tags
    git -C "$INSTALL_DIR" pull --ff-only
elif [ -e "$INSTALL_DIR" ]; then
    echo "FATAL: $INSTALL_DIR exists but is not a git checkout." >&2
    echo "Move/remove it or specify a different --dir=." >&2
    exit 1
else
    echo "[install] cloning $REPO_URL into $INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ---- secrets generation ---------------------------------------------

if [ ! -f .env ]; then
    cp .env.example .env
    echo "[install] created .env from .env.example"
else
    echo "[install] .env already exists — preserving existing values"
fi

# Generate any secret that's still at the placeholder value.
gen_secret() {
    local key="$1"
    local prefix="$2"
    local current
    current=$(grep -E "^${key}=" .env | head -1 | cut -d= -f2-)
    if [ -z "$current" ] || [[ "$current" == ${prefix}* ]]; then
        local new_val
        new_val=$(openssl rand -hex 32)
        if grep -qE "^${key}=" .env; then
            sed -i.bak "s|^${key}=.*|${key}=${new_val}|" .env
        else
            echo "${key}=${new_val}" >> .env
        fi
        echo "  generated $key"
    fi
}

echo "[install] generating any missing secrets"
gen_secret DB_PASSWORD change_this
gen_secret DB_ROOT_PASSWORD change_this
gen_secret JWT_SECRET change_this
gen_secret TUS_HOOK_SECRET change_this
rm -f .env.bak

set_kv() {
    local key="$1"
    local val="$2"
    if grep -qE "^${key}=" .env; then
        sed -i.bak "s|^${key}=.*|${key}=${val}|" .env
    else
        echo "${key}=${val}" >> .env
    fi
}

set_kv APP_URL "$APP_URL"
set_kv FH_TAG "$FH_TAG"
rm -f .env.bak

chmod 600 .env

# ---- state dir ownership ---------------------------------------------
# The backend container runs as appuser (UID 1000); the updater-shim
# runs as root. Both share data/updater/ via bind mount, and backend
# needs to write the update-request JSON. If we let the shim create
# the dir first, it ends up root-owned and backend can't write. Force
# UID 1000 here so backend wins from the first compose up.
# (A one-shot privileged container does the chown — saves us from
#  requiring `sudo` in the installer itself.)
echo "[install] ensuring data/updater is writable by the backend (UID 1000)"
mkdir -p data/updater
docker run --rm -v "$(pwd)/data/updater:/state" alpine chown -R 1000:1000 /state >/dev/null

# ---- pull + up -d ----------------------------------------------------

echo "[install] pulling images (tag=$FH_TAG)"
docker compose pull

echo "[install] starting stack"
docker compose up -d

cat <<EOF

═══════════════════════════════════════════════════════════════════
  fileHeron is starting on tag=$FH_TAG.

  Once the backend is healthy (typically ~30s), visit:
    $APP_URL/setup

  ...to create your first admin account through the web wizard.

  Operator commands:
    docker compose ps          # service status
    docker compose logs -f     # tail logs
    docker compose down        # stop stack
═══════════════════════════════════════════════════════════════════

EOF
