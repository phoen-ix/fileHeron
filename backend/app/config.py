"""Pydantic-Settings config - fail-fast on insecure defaults in production.

Environment variables that must be set in production are validated below.
ENVIRONMENT=production additionally forces COOKIE_SECURE=true.
"""
from __future__ import annotations

import os
import re
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
    # SQLAlchemy connection-pool sizing. Defaults raise SQLAlchemy's own
    # 5+10 to 10+20 so a burst of concurrent share-list / admin requests
    # doesn't churn temp overflow connections. Ensure MariaDB
    # `max_connections` >= app_replicas * (DB_POOL_SIZE + DB_POOL_MAX_OVERFLOW).
    DB_POOL_SIZE: int = 10
    DB_POOL_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT_SEC: int = 30

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
    # How long pending/expired invite_tokens rows linger before the
    # daily cleanup_pending_invites cron purges them. Consumed invites
    # are kept indefinitely (proof-of-onboarding for the resulting
    # user row). Default 14 days from created_at.
    INVITE_RETENTION_DAYS: int = 14

    # --- Operational retention (operational audit Wave 3) --------------------
    # The prune_history cron deletes rows older than these windows. Audit log
    # default 365d is conservative; bump for regulated environments. Set any
    # to 0 to disable that table's pruning.
    AUDIT_LOG_RETENTION_DAYS: int = 365
    DOWNLOAD_LOG_RETENTION_DAYS: int = 90
    # Mail log rows carry (masked) email content, so a tighter default than the
    # audit log is appropriate. 0 disables pruning.
    EMAIL_LOG_RETENTION_DAYS: int = 90
    LOGIN_ATTEMPT_RETENTION_DAYS: int = 30
    # Webhook delivery attempt log (v1.19.0). 0 disables pruning.
    WEBHOOK_DELIVERY_RETENTION_DAYS: int = 30
    # In-app notifications: once read, the bell hides them on next load; this
    # is how long a READ notification lingers in the DB before the daily
    # cleanup_read_notifications cron hard-deletes it (not instant - keeps a
    # short read-history window for support/debug). 0 disables the cron.
    NOTIFICATION_READ_RETENTION_DAYS: int = 3
    # The purge_old_quarantine cron unlinks bytes (keeps the file row as a
    # historical marker) when quarantined longer than this. 0 disables.
    QUARANTINE_PURGE_AFTER_DAYS: int = 90
    # The reclaim_orphaned_files cron frees bytes + quota for files whose
    # share has been revoked/deleted longer than this (grace window after a
    # soft revoke). 0 disables auto-reclaim (admins still reclaim manually).
    ORPHAN_RECLAIM_AFTER_DAYS: int = 7
    # Abandoned TUS uploads (no DB row, or row stuck in `uploading` state)
    # older than this get unlinked from /data/uploads.
    TUS_UPLOAD_ABANDONED_AFTER_HOURS: int = 24
    # A `files` row stuck in `uploading` longer than this is treated as an
    # abandoned/failed upload: cleanup_stale_uploads reaps the file and flips
    # the now-empty share to `failed`. Short because legit uploads finish well
    # under the ~1h TUS resume window.
    UPLOAD_STALE_AFTER_HOURS: int = 3

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
    # EHLO/HELO name announced to the MTA. Empty = aiosmtplib uses the
    # container's socket.getfqdn() (unpredictable). Set to a real,
    # resolvable hostname when a strict MTA rejects with "Client host
    # rejected". Admin-overridable via the smtp.helo_hostname kv key.
    SMTP_HELO_HOST: str = ""

    # --- IMAP (inbound mailbox, v1.27.0) -------------------------------------
    # All admin-overridable via imap.* kv keys; off until imap.enabled is set.
    IMAP_HOST: str = ""
    IMAP_PORT: int = 993
    IMAP_USER: str = ""
    IMAP_PASSWORD: str = ""
    IMAP_TLS_MODE: str = "implicit"  # 'implicit' (993) | 'starttls' (143) | 'none'
    IMAP_MAILBOX: str = "INBOX"
    IMAP_MESSAGE_RETENTION_DAYS: int = 90

    # --- Rate limits ---------------------------------------------------------
    # LOGIN gates /api/auth/login + /login/recovery (services/rate_limit.py).
    # REGISTER gates /register-from-invite, /forgot-password, /verify-email
    # (routers/auth.py). There is no API-wide bearer-rate-limit today; if you
    # add one, model it on `services/rate_limit.py::check_ip_allowed`.
    RATE_LIMIT_LOGIN: int = 10
    RATE_LIMIT_REGISTER: int = 3
    # Login lockout knobs (promoted from rate_limit.py module constants so
    # they're admin-tunable via the settings registry). The per-IP login
    # window, the consecutive-failure threshold that locks an account, and
    # how long that lock lasts.
    LOGIN_RATE_WINDOW_SEC: int = 900  # 15 min
    LOCKOUT_THRESHOLD: int = 5
    LOCKOUT_DURATION_MIN: int = 15

    # --- Phase 3a - upload pipeline ------------------------------------------
    # Shared HMAC secret for tusd ↔ backend (envelope authorisation).
    TUS_HOOK_SECRET: str = "change-me-tus-hook-secret-min-32-chars-_______________"
    # Optional CSV allowlist for the source IP of `/api/internal/tus-hooks`
    # requests (defense-in-depth; HMAC envelope is still required). Leave
    # empty to accept any source. In compose, set to the tusd container's
    # network address (e.g. via `getent hosts tusd`).
    TUS_HOOK_ALLOWED_IPS: str = ""
    # Cap for the direct-upload endpoint. Larger files must use TUS.
    MAX_DIRECT_UPLOAD_BYTES: int = 104857600  # 100 MB
    # TTL of the signed `?dt=` download URL the SPA hands to the browser.
    # Longer = a dropped browser download can be resumed (the native
    # download manager re-requests the SAME url with a Range header) for
    # longer; shorter = a url leaked into a proxy/access log is usable for
    # a smaller window. Admin-tunable via the registry. Default 15 min.
    DOWNLOAD_SIGNED_URL_TTL_SEC: int = 900
    # Maintenance-mode postpone: how long the deferred-update drain worker waits
    # for in-flight transfers to finish before applying the update anyway.
    # Admin-tunable via the registry. Default 30 min.
    UPDATES_DRAIN_MAX_WAIT_MIN: int = 30
    # Where finalized files live + tusd's working dir + AV quarantine.
    # Must all be on the SAME filesystem (atomic os.rename across them).
    STORAGE_ROOT: str = "/data/files"
    TUS_UPLOAD_DIR: str = "/data/uploads"
    QUARANTINE_DIR: str = "/data/quarantine"

    # --- Storage backend (v1.22.0) -------------------------------------------
    # "local" (default - the bind mount above) or "s3" (any S3-compatible store).
    # On s3, uploads stream to the bucket, downloads 307-redirect to a presigned
    # URL, AV scans via clamd INSTREAM, and quarantine moves between key prefixes.
    STORAGE_BACKEND: str = "local"
    S3_ENDPOINT_URL: str = ""        # blank = AWS default; set for MinIO/localstack
    S3_BUCKET: str = ""
    S3_REGION: str = "us-east-1"
    S3_ACCESS_KEY_ID: str = ""       # blank = boto3 default credential chain
    S3_SECRET_ACCESS_KEY: str = ""
    S3_KEY_PREFIX: str = ""          # optional key namespace within the bucket
    # Public path the browser uses to reach tusd (proxied by nginx in prod /
    # Vite in dev). Trailing slash matters for tusd's -base-path.
    TUS_PUBLIC_BASE: str = "/uploads/"
    # Low-disk degradation: when free space on STORAGE_ROOT drops below
    # EITHER threshold, the hourly disk_check cron flips the
    # `storage.critical_low` kv flag + alerts admins, and new uploads are
    # refused with 507. Downloads are unaffected. Both admin-tunable.
    STORAGE_LOW_THRESHOLD_PERCENT: int = 5
    STORAGE_LOW_THRESHOLD_BYTES: int = 10 * 1024**3  # 10 GiB

    # --- Metrics endpoint (Prometheus) ---------------------------------------
    # GET /api/metrics is gated: a request must carry
    # `Authorization: Bearer <METRICS_BEARER_TOKEN>` OR originate from an IP in
    # METRICS_ALLOWED_IPS (comma-separated IPs / CIDRs). With both empty the
    # endpoint is effectively disabled (every request 401s). The rendered text
    # is cached for METRICS_CACHE_TTL_SEC to bound DB load under frequent scrapes.
    METRICS_BEARER_TOKEN: str = ""
    METRICS_ALLOWED_IPS: str = ""
    METRICS_CACHE_TTL_SEC: int = 60

    # --- Phase 6b - 2FA enforcement (env fallback) ----------------------------
    # `none` = optional (default), `admins` = required for admins, `all` = required
    # for every user. Used as the fallback when `app_settings.twofa.required_*`
    # kv keys are unset (admin can override at runtime via /admin/settings/twofa).
    REQUIRE_2FA: str = "none"

    # --- Phase 7 - HIBP ------------------------------------------------------
    # When false, password-breach checks are disabled (air-gapped deploys).
    HIBP_ENABLED: bool = True
    # (OIDC SSO is DB-configured multi-provider - `oidc_providers` table, admin
    # UI /admin/settings/sso - so there are no OIDC env vars beyond the escape
    # hatch below. BACKUP_RESTIC_* are host-side vars read only by
    # scripts/backup.sh, never by the app.)
    #
    # Every outbound OIDC call (discovery, JWKS, and the token exchange) used to
    # pass require_https=False, so an `http://` issuer was accepted. The token
    # exchange POSTs the provider's CLIENT SECRET to the discovery-supplied token
    # endpoint, so a plaintext issuer put that secret on the wire in cleartext on
    # every single login - and a network attacker could also rewrite the
    # discovery document to point the secret-bearing POST wherever they liked
    # (audit 2026-07-30).
    #
    # HTTPS is now required. This opt-out exists only for a self-hosted IdP
    # reachable exclusively over a trusted private network with no TLS; it is
    # unsafe on any other topology and is deliberately env-only (not
    # admin-tunable) so it cannot be flipped from a compromised admin session.
    OIDC_ALLOW_INSECURE_HTTP: bool = False

    # --- Phase 8 - WebAuthn / passkeys ---------------------------------------
    # Relying-Party identifier MUST match the public hostname (no scheme,
    # no port). Platform authenticators (Touch ID, Windows Hello) bind
    # credentials to this string forever - change it and you invalidate
    # every registered credential.
    WEBAUTHN_RP_ID: str = "localhost"
    WEBAUTHN_RP_NAME: str = "fileHeron"
    # Comma-separated list of allowed origins for the WebAuthn flow. Empty
    # falls back to APP_URL.
    WEBAUTHN_ORIGINS: str = ""

    # --- Phase 5 - antivirus + public links ----------------------------------
    # ClamAV daemon endpoint inside the docker network.
    CLAMAV_HOST: str = "clamav"
    CLAMAV_PORT: int = 3310
    # Skip the AV scan entirely (e.g. for tests, CI). When true, every
    # uploaded file is auto-marked `clean` immediately.
    AV_SKIP: bool = False
    # Largest file clamd will actually scan. clamd reports "clean" for a file
    # past its limit (it just stops scanning), so a "clean" verdict above this
    # threshold is not evidence of anything and must not be recorded as one.
    #
    # This is NOT freely configurable upward: clamd stores MaxFileSize in an
    # int and silently clamps it to INT_MAX, so `MaxFileSize 30G` in
    # docker/clamav/clamd.conf really becomes 2147483645 bytes. Its own startup
    # log says so:
    #     Limits: Global size limit set to 32212254720 bytes.   <- MaxScanSize
    #     Limits: File size limit set to 2147483645 bytes.      <- CLAMPED
    # The default below therefore matches clamd's real ceiling, not the
    # configured one. Setting it higher does not make clamd scan more; it just
    # makes fileHeron trust verdicts clamd never produced (audit 2026-07-30,
    # which is the original H3 bug surviving its own fix one order of magnitude
    # up).
    #
    # Files above this are still served, but are recorded with
    # `files.av_unscanned = True` and surfaced as unscanned rather than clean.
    AV_MAX_SCAN_BYTES: int = 2147483645  # clamd's INT_MAX clamp on MaxFileSize
    # Public-link tunables.
    PUBLIC_LINK_BASE_PATH: str = "/d"
    PUBLIC_LINK_PASSWORD_RATE_LIMIT: int = 10  # max attempts per (link, IP) per window
    PUBLIC_LINK_PASSWORD_WINDOW_SEC: int = 900  # 15 minutes
    PUBLIC_LINK_LOCKOUT_SEC: int = 900  # link locked for 15 min after lockout

    # --- Anomaly detection (v1.20.0, heuristic / GeoIP-free) ------------------
    # Hourly anomaly_check cron; thresholds admin-tunable. Alerts only, never
    # auto-blocks. Set ANOMALY_ENABLED=false to disable the cron entirely.
    ANOMALY_ENABLED: bool = True
    ANOMALY_MASS_DOWNLOAD_THRESHOLD: int = 100   # downloads / user / 15 min
    ANOMALY_MULTI_NETWORK_THRESHOLD: int = 4     # distinct networks / user / 30 min
    ANOMALY_LOGIN_FAILURE_THRESHOLD: int = 50    # failed logins / IP / 15 min

    # --- Error alerting (email admins on server errors) ----------------------
    # Off by default; admins enable + tune on /admin/settings/error-alerts. The
    # cooldown dedups identical errors per signature; the hourly cap bounds a
    # burst regardless of signature. Both are registry tunables (admin-editable).
    ERROR_ALERT_COOLDOWN_MINUTES: int = 15   # suppress an identical error this long
    ERROR_ALERT_MAX_PER_HOUR: int = 20       # hard ceiling on alert emails / hour
    # Error LOG retention (the error_log table + admin viewer). 0 disables the
    # daily prune. Logging itself is on by default for 5xx (error_log.enabled).
    ERROR_LOG_RETENTION_DAYS: int = 90
    # Max 4xx error events captured into the log per minute (global front-guard in
    # middleware/errors.py, mirrored to the edge nginx limit_req). Raise for fuller
    # scan visibility; it bounds the worst-case log-write rate during a probe storm.
    ERROR_LOG_SCAN_CAPTURE_PER_MIN: int = 300

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

# Every secret placeholder shipped in .env.example begins "change-" or
# "change_". Kept module-level so the regression test can assert it against the
# real .env.example rather than against a copy of the strings.
_PLACEHOLDER_RE = re.compile(r"^change[-_]", re.IGNORECASE)


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

    # Matched by PREFIX, not by literal. This used to compare against exact
    # strings, and those strings drifted away from the ones .env.example
    # actually ships - so `cp .env.example .env` + ENVIRONMENT=production booted
    # on the published JWT_SECRET and TUS_HOOK_SECRET, with every token
    # forgeable and every Fernet field decryptable. Every placeholder we ship
    # begins "change-" or "change_"; a real random secret doing the same is not
    # a practical concern, and the failure mode is a loud boot error.
    # tests/test_config_placeholders.py loads .env.example verbatim and asserts
    # each value here is still caught - that is what stops the drift recurring.
    _secret_fields = [
        ("JWT_SECRET", settings.JWT_SECRET),
        ("DB_PASSWORD", settings.DB_PASSWORD),
        # TUS_HOOK_SECRET is the load-bearing HMAC secret for the internal tusd
        # webhook - at its default an attacker who knows the placeholder can
        # forge upload envelopes.
        ("TUS_HOOK_SECRET", settings.TUS_HOOK_SECRET),
        # Not a Settings field (only the db container consumes it), but it does
        # reach the backend via `env_file: .env`, which makes this the one
        # boot-time place that can refuse a published default. Absent = no
        # opinion, so deployments that don't pass it through still boot.
        ("DB_ROOT_PASSWORD", os.environ.get("DB_ROOT_PASSWORD", "")),
    ]
    for name, value in _secret_fields:
        if value and _PLACEHOLDER_RE.match(value.strip()):
            _fail_or_warn(
                f"{name} is still at a shipped placeholder value. Set it to a "
                "strong random value (e.g. `openssl rand -hex 32`)."
            )

    if len(settings.JWT_SECRET) < 32:
        _fail_or_warn("JWT_SECRET is too short (min 32 chars).")
    if len(settings.TUS_HOOK_SECRET) < 32:
        _fail_or_warn("TUS_HOOK_SECRET is too short (min 32 chars).")

    # S3 backend selected but unconfigured → fail fast rather than 500 on first upload.
    if settings.STORAGE_BACKEND.strip().lower() == "s3" and not settings.S3_BUCKET:
        _fail_or_warn("STORAGE_BACKEND=s3 but S3_BUCKET is unset.")

    # AV_SKIP is meant for tests - in production, uploads must be
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
