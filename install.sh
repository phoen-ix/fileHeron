#!/usr/bin/env bash
# fileHeron installer - clones (or updates) the repo, generates random
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
    echo "[install] $INSTALL_DIR already exists - pulling latest"
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
    # umask FIRST, then copy: `cp` creates the file with the ambient umask, so
    # .env existed world-readable for the whole window in which the secrets were
    # generated into it, and the chmod 600 at the end of the script closed the
    # door after they were already on disk. On a multi-user host that window is
    # the entire install (audit 2026-07-30, ops-12).
    (umask 077; cp .env.example .env)
    chmod 600 .env
    created_env=1
    echo "[install] created .env from .env.example"
else
    created_env=0
    echo "[install] .env already exists - preserving existing values"
fi

# Generate any secret that's still at a placeholder value.
#
# Matched by PREFIX, not by a caller-supplied literal. This used to be called
# with `change_this` for all four keys while .env.example shipped
# `change_me_in_production` and `change_root_password`, so DB_PASSWORD and
# DB_ROOT_PASSWORD were silently never regenerated: a fresh install kept the
# published database credentials, and the backend then refused to boot on the
# DB_PASSWORD placeholder (audit 2026-07-30). Keep this rule in lockstep with
# backend/app/config.py::_PLACEHOLDER_RE.
# `sed -i.bak` writes .env.bak with the ambient umask and it holds the PREVIOUS
# contents - which after the first substitution already includes generated
# secrets. Tighten it as soon as it can exist; it is removed below either way.
_secure_env_files() {
    chmod 600 .env 2>/dev/null || true
    [ -f .env.bak ] && chmod 600 .env.bak 2>/dev/null || true
}

gen_secret() {
    local key="$1"
    local current
    current=$(grep -E "^${key}=" .env | head -1 | cut -d= -f2-)
    if [ -z "$current" ] || [[ "${current,,}" == change[-_]* ]]; then
        local new_val
        new_val=$(openssl rand -hex 32)
        if grep -qE "^${key}=" .env; then
            sed -i.bak "s|^${key}=.*|${key}=${new_val}|" .env
        else
            echo "${key}=${new_val}" >> .env
        fi
        _secure_env_files
        echo "  generated $key"
    fi
}

echo "[install] generating any missing secrets"
gen_secret DB_PASSWORD
gen_secret DB_ROOT_PASSWORD
gen_secret JWT_SECRET
gen_secret TUS_HOOK_SECRET
rm -f .env.bak

# Refuse to continue if any of them somehow survived - booting on a published
# credential is worse than failing the install loudly.
for _k in DB_PASSWORD DB_ROOT_PASSWORD JWT_SECRET TUS_HOOK_SECRET; do
    _v=$(grep -E "^${_k}=" .env | head -1 | cut -d= -f2-)
    if [ -z "$_v" ] || [[ "${_v,,}" == change[-_]* ]]; then
        echo "[install] FATAL: $_k is still at a placeholder value. Aborting." >&2
        exit 1
    fi
done
unset _k _v

set_kv() {
    local key="$1"
    local val="$2"
    if grep -qE "^${key}=" .env; then
        sed -i.bak "s|^${key}=.*|${key}=${val}|" .env
    else
        # Append when the key is absent. An operator upgrading from an older
        # release has an .env predating whatever key this release added, and
        # silently doing nothing here would leave the setting at its code
        # default with no sign of it in the file they read (ops-13).
        echo "${key}=${val}" >> .env
    fi
    _secure_env_files
    rm -f .env.bak
}

set_kv APP_URL "$APP_URL"
set_kv FH_TAG "$FH_TAG"

# A fresh .env from .env.example is DEVELOPMENT-mode: insecure cookies, /docs
# exposed, and a seeded test account with a known password. Harden it for
# production on first install. On a re-run we leave the operator's .env alone
# (only APP_URL/FH_TAG above are refreshed from the install args).
if [ "$created_env" = 1 ]; then
    # WebAuthn RP ID = the bare host of APP_URL (no scheme, port, or path);
    # passkeys break if it doesn't match the domain the browser sees.
    rp_host="${APP_URL#*://}"; rp_host="${rp_host%%/*}"; rp_host="${rp_host%%:*}"
    set_kv ENVIRONMENT production
    set_kv COOKIE_SECURE true
    set_kv WEBAUTHN_RP_ID "$rp_host"
    # Dev-only seed creds: inert under ENVIRONMENT=production, blanked so a later
    # flip to development can't silently seed a known-password account.
    set_kv TEST_ACCOUNT_EMAIL ""
    set_kv TEST_ACCOUNT_PASSWORD ""
    echo "  hardened .env for production (ENVIRONMENT=production, COOKIE_SECURE=true, WEBAUTHN_RP_ID=$rp_host)"
fi
rm -f .env.bak

chmod 600 .env

# ---- bind-mount dir ownership ----------------------------------------
# backend/worker/tusd all run as UID 1000; the updater-shim + clamav run
# as root. They share data/{updater,uploads,quarantine,files} via bind
# mounts. The footgun: if any of these dirs is MISSING when compose
# starts, the root docker daemon creates the bind-mount source as
# root:root, and the UID-1000 containers can no longer write
# (tusd: "open /data/uploads/...: permission denied"). Pre-create them
# and force UID 1000 here so the app wins from the first compose up.
# (A one-shot privileged container does the chown - saves us from
#  requiring `sudo` in the installer itself.)
echo "[install] ensuring data bind-mount dirs are writable by the app (UID 1000)"
for d in updater uploads quarantine files; do mkdir -p "data/$d"; done
docker run --rm -v "$(pwd)/data:/data" alpine \
    chown -R 1000:1000 /data/updater /data/uploads /data/quarantine /data/files >/dev/null

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
