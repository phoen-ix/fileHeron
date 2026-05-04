"""Pydantic-Settings config — fail-fast on insecure defaults in production.

Environment variables that must be set in production are validated below.
ENVIRONMENT=production additionally forces COOKIE_SECURE=true.
"""
from __future__ import annotations

import os
import sys
import warnings
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Environment ---------------------------------------------------------
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    APP_URL: str = "http://localhost:8080"
    APP_NAME: str = "fileHeron"

    # --- Database ------------------------------------------------------------
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_USER: str = "fileheron_app"
    DB_PASSWORD: str = "change_me_in_production"
    DB_NAME: str = "fileheron"

    # --- Redis ---------------------------------------------------------------
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    # --- JWT / cookies -------------------------------------------------------
    JWT_SECRET: str = "change-me-in-production-min-32-chars"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    COOKIE_SECURE: bool = False
    # Per-user concurrent session cap. On login, if the user already has
    # this many active (non-revoked, non-expired) refresh tokens, the
    # oldest is auto-revoked before the new one is created.
    MAX_ACTIVE_SESSIONS_PER_USER: int = 10
    # How long revoked refresh-token rows stick around for forensics
    # before the hourly cleanup hard-deletes them.
    REFRESH_TOKEN_RETENTION_DAYS: int = 30

    # --- Argon2id parameters -------------------------------------------------
    ARGON2_TIME_COST: int = 3
    ARGON2_MEMORY_COST_KIB: int = 65536
    ARGON2_PARALLELISM: int = 2

    # --- Admin bootstrap -----------------------------------------------------
    ADMIN_BOOTSTRAP_EMAIL: str = ""
    ADMIN_BOOTSTRAP_PASSWORD: str = ""

    # --- Dev test account (consumed by scripts/seed_dev.py + entrypoint) ------
    TEST_ACCOUNT_EMAIL: str = ""
    TEST_ACCOUNT_PASSWORD: str = ""
    TEST_ACCOUNT_DISPLAY_NAME: str = ""

    # --- SMTP ----------------------------------------------------------------
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = "noreply@fileheron.local"
    SMTP_FROM_NAME: str = "fileHeron"

    # --- Rate limits (Phase 1b uses these) -----------------------------------
    RATE_LIMIT_LOGIN: int = 10
    RATE_LIMIT_REGISTER: int = 3
    RATE_LIMIT_API: int = 100

    # --- Phase 3a — upload pipeline ------------------------------------------
    # Shared HMAC secret for tusd ↔ backend (envelope authorisation).
    TUS_HOOK_SECRET: str = "change-me-tus-hook-secret-min-32-chars-_______________"
    # Optional CSV allowlist for the source IP of `/api/internal/tus-hooks`
    # requests (defense-in-depth; HMAC envelope is still required). Leave
    # empty to accept any source. In compose, set to the tusd container's
    # network address (e.g. via `getent hosts tusd`).
    TUS_HOOK_ALLOWED_IPS: str = ""
    # Cap for the direct-upload endpoint. Larger files must use TUS.
    MAX_DIRECT_UPLOAD_BYTES: int = 104857600  # 100 MB
    # Where finalized files live + tusd's working dir + AV quarantine.
    # Must all be on the SAME filesystem (atomic os.rename across them).
    STORAGE_ROOT: str = "/data/files"
    TUS_UPLOAD_DIR: str = "/data/uploads"
    QUARANTINE_DIR: str = "/data/quarantine"
    # Public path the browser uses to reach tusd (proxied by nginx in prod /
    # Vite in dev). Trailing slash matters for tusd's -base-path.
    TUS_PUBLIC_BASE: str = "/uploads/"

    # --- Phase 6b — 2FA enforcement (env fallback) ----------------------------
    # `none` = optional (default), `admins` = required for admins, `all` = required
    # for every user. Used as the fallback when `app_settings.twofa.required_*`
    # kv keys are unset (admin can override at runtime via /admin/settings/twofa).
    REQUIRE_2FA: str = "none"

    # --- Phase 7 — HIBP / OIDC / backup --------------------------------------
    # When false, password-breach checks are disabled (air-gapped deploys).
    HIBP_ENABLED: bool = True
    # Empty issuer URL = OIDC disabled. Operator sets this to e.g.
    # "https://idp.example.com/realms/fileheron".
    OIDC_ISSUER_URL: str = ""
    OIDC_CLIENT_ID: str = ""
    OIDC_CLIENT_SECRET: str = ""
    # Dot-separated path in the ID-token claims dict pointing at the groups
    # list. Different IdPs put it in different places.
    OIDC_GROUPS_CLAIM: str = "groups"
    # Comma-separated group names. First match wins (admin > employee).
    OIDC_ADMIN_GROUPS: str = ""
    OIDC_EMPLOYEE_GROUPS: str = ""
    # Restic-encrypted backup target (optional remote push).
    BACKUP_RESTIC_REPO: str = ""
    BACKUP_RESTIC_PASSWORD: str = ""

    # --- Phase 8 — WebAuthn / passkeys ---------------------------------------
    # Relying-Party identifier MUST match the public hostname (no scheme,
    # no port). Platform authenticators (Touch ID, Windows Hello) bind
    # credentials to this string forever — change it and you invalidate
    # every registered credential.
    WEBAUTHN_RP_ID: str = "localhost"
    WEBAUTHN_RP_NAME: str = "fileHeron"
    # Comma-separated list of allowed origins for the WebAuthn flow. Empty
    # falls back to APP_URL.
    WEBAUTHN_ORIGINS: str = ""

    # --- Phase 5 — antivirus + public links ----------------------------------
    # ClamAV daemon endpoint inside the docker network.
    CLAMAV_HOST: str = "clamav"
    CLAMAV_PORT: int = 3310
    # Skip the AV scan entirely (e.g. for tests, CI). When true, every
    # uploaded file is auto-marked `clean` immediately.
    AV_SKIP: bool = False
    # Public-link tunables.
    PUBLIC_LINK_BASE_PATH: str = "/d"
    PUBLIC_LINK_PASSWORD_RATE_LIMIT: int = 10  # max attempts per (link, IP) per window
    PUBLIC_LINK_PASSWORD_WINDOW_SEC: int = 900  # 15 minutes
    PUBLIC_LINK_LOCKOUT_SEC: int = 900  # link locked for 15 min after lockout

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{quote_plus(self.DB_USER)}:{quote_plus(self.DB_PASSWORD)}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            "?charset=utf8mb4"
        )

    @property
    def _env_normalized(self) -> str:
        return self.ENVIRONMENT.strip().lower()

    @property
    def is_production(self) -> bool:
        # Accept "prod" as an alias and tolerate accidental whitespace /
        # mixed case so a typo like "Production " doesn't silently disable
        # COOKIE_SECURE force-true, AV_SKIP fail-fast, and the docs-disable
        # below.
        return self._env_normalized in _PRODUCTION_ALIASES

    model_config = {"env_file": ".env", "extra": "ignore"}


_PRODUCTION_ALIASES = frozenset({"production", "prod"})
_KNOWN_ENVIRONMENTS = _PRODUCTION_ALIASES | frozenset({"development", "test"})


settings = Settings()


# ---- Fail-fast on insecure defaults in production ---------------------------
def _fail_or_warn(message: str) -> None:
    if settings.is_production:
        sys.exit(f"FATAL: {message}")
    warnings.warn(message, stacklevel=2)


if os.environ.get("PYTEST_CURRENT_TEST") is None:
    # Hard-exit on a misspelled ENVIRONMENT so the prod safety rails below
    # aren't silently disabled (e.g. ENVIRONMENT="Production " left
    # is_production False and shipped insecure cookies + docs exposed).
    if settings._env_normalized not in _KNOWN_ENVIRONMENTS:
        sys.exit(
            f"FATAL: ENVIRONMENT={settings.ENVIRONMENT!r} is not recognised. "
            f"Use one of: {', '.join(sorted(_KNOWN_ENVIRONMENTS))}."
        )

    _insecure_defaults = [
        ("change-me-in-production-min-32-chars", "JWT_SECRET", settings.JWT_SECRET),
        ("change_me_in_production", "DB_PASSWORD", settings.DB_PASSWORD),
    ]
    for placeholder, name, value in _insecure_defaults:
        if value == placeholder:
            _fail_or_warn(f"{name} is unset (still at placeholder). Set it to a strong random value.")

    if len(settings.JWT_SECRET) < 32:
        _fail_or_warn("JWT_SECRET is too short (min 32 chars).")

    # AV_SKIP is meant for tests — in production, uploads must be
    # scanned before they're available for download. If both are
    # set, we crash on boot rather than ship infected files.
    if settings.AV_SKIP:
        _fail_or_warn(
            "AV_SKIP=true in production. Antivirus scanning would be "
            "disabled and uploaded files served as `clean` without "
            "inspection. Set AV_SKIP=false."
        )

# In production, force secure cookies even if env says otherwise.
if settings.is_production:
    settings.COOKIE_SECURE = True
