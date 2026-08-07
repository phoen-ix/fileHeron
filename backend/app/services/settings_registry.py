"""Registry of runtime-tunable settings (".env → admin UI" migration).

Each `Tunable` maps an `app_settings` kv key to its `config.Settings`
env-default attribute, a type, a UI group, and (for ints) safe bounds.
`effective(db, key)` returns the live value: the kv override if present
(clamped to bounds), otherwise the env default. Reads are live per call -
no boot cache - so an admin change applies without a redeploy, exactly
like the other kv-overlay settings (SMTP, 2FA, site URL, …).

Secrets and infra/boot-critical settings are deliberately NOT in here;
they stay env-only. The generic admin endpoints in routers/admin/settings.py
only ever expose/accept keys present in this registry.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..config import settings as _env
from . import settings as settings_svc

Kind = str  # "int" | "bool" | "str"


@dataclass(frozen=True)
class Tunable:
    key: str            # app_settings kv key (settings_svc.Keys.*)
    env_attr: str       # config.Settings attribute holding the default
    kind: Kind          # "int" | "bool" | "str"
    group: str          # UI grouping
    min: int | None = None
    max: int | None = None


K = settings_svc.Keys

TUNABLES: list[Tunable] = [
    # --- Sessions & auth ---
    Tunable(K.ACCESS_TOKEN_EXPIRE_MINUTES, "ACCESS_TOKEN_EXPIRE_MINUTES", "int", "sessions", 5, 1440),
    Tunable(K.REFRESH_TOKEN_EXPIRE_DAYS, "REFRESH_TOKEN_EXPIRE_DAYS", "int", "sessions", 1, 365),
    Tunable(K.MAX_ACTIVE_SESSIONS_PER_USER, "MAX_ACTIVE_SESSIONS_PER_USER", "int", "sessions", 1, 100),
    # --- Rate limit & lockout ---
    Tunable(K.RATE_LIMIT_LOGIN, "RATE_LIMIT_LOGIN", "int", "rate_limits", 1, 1000),
    Tunable(K.RATE_LIMIT_REGISTER, "RATE_LIMIT_REGISTER", "int", "rate_limits", 1, 1000),
    Tunable(K.LOGIN_RATE_WINDOW_SEC, "LOGIN_RATE_WINDOW_SEC", "int", "rate_limits", 30, 86400),
    Tunable(K.LOCKOUT_THRESHOLD, "LOCKOUT_THRESHOLD", "int", "rate_limits", 1, 100),
    Tunable(K.LOCKOUT_DURATION_MIN, "LOCKOUT_DURATION_MIN", "int", "rate_limits", 1, 1440),
    Tunable(K.PUBLIC_LINK_PASSWORD_RATE_LIMIT, "PUBLIC_LINK_PASSWORD_RATE_LIMIT", "int", "rate_limits", 1, 1000),
    Tunable(K.PUBLIC_LINK_PASSWORD_WINDOW_SEC, "PUBLIC_LINK_PASSWORD_WINDOW_SEC", "int", "rate_limits", 30, 86400),
    Tunable(K.PUBLIC_LINK_LOCKOUT_SEC, "PUBLIC_LINK_LOCKOUT_SEC", "int", "rate_limits", 30, 86400),
    # --- Retention (0 disables where the cron supports it) ---
    Tunable(K.REFRESH_TOKEN_RETENTION_DAYS, "REFRESH_TOKEN_RETENTION_DAYS", "int", "retention", 1, 3650),
    Tunable(K.INVITE_RETENTION_DAYS, "INVITE_RETENTION_DAYS", "int", "retention", 1, 3650),
    Tunable(K.AUDIT_LOG_RETENTION_DAYS, "AUDIT_LOG_RETENTION_DAYS", "int", "retention", 0, 3650),
    Tunable(K.DOWNLOAD_LOG_RETENTION_DAYS, "DOWNLOAD_LOG_RETENTION_DAYS", "int", "retention", 0, 3650),
    Tunable(K.EMAIL_LOG_RETENTION_DAYS, "EMAIL_LOG_RETENTION_DAYS", "int", "retention", 0, 3650),
    Tunable(K.IMAP_MESSAGE_RETENTION_DAYS, "IMAP_MESSAGE_RETENTION_DAYS", "int", "retention", 0, 3650),
    Tunable(K.LOGIN_ATTEMPT_RETENTION_DAYS, "LOGIN_ATTEMPT_RETENTION_DAYS", "int", "retention", 0, 3650),
    Tunable(K.WEBHOOK_DELIVERY_RETENTION_DAYS, "WEBHOOK_DELIVERY_RETENTION_DAYS", "int", "retention", 0, 3650),
    Tunable(K.ERROR_LOG_RETENTION_DAYS, "ERROR_LOG_RETENTION_DAYS", "int", "retention", 0, 3650),
    Tunable(K.PUBLIC_LINK_ATTEMPT_RETENTION_DAYS, "PUBLIC_LINK_ATTEMPT_RETENTION_DAYS", "int", "retention", 0, 3650),
    Tunable(K.IP_BLOCK_RETENTION_DAYS, "IP_BLOCK_RETENTION_DAYS", "int", "retention", 0, 3650),
    Tunable(K.NOTIFICATION_READ_RETENTION_DAYS, "NOTIFICATION_READ_RETENTION_DAYS", "int", "retention", 0, 3650),
    Tunable(K.QUARANTINE_PURGE_AFTER_DAYS, "QUARANTINE_PURGE_AFTER_DAYS", "int", "retention", 0, 3650),
    Tunable(K.ORPHAN_RECLAIM_AFTER_DAYS, "ORPHAN_RECLAIM_AFTER_DAYS", "int", "retention", 0, 3650),
    Tunable(K.TUS_UPLOAD_ABANDONED_AFTER_HOURS, "TUS_UPLOAD_ABANDONED_AFTER_HOURS", "int", "retention", 1, 8760),
    Tunable(K.UPLOAD_STALE_AFTER_HOURS, "UPLOAD_STALE_AFTER_HOURS", "int", "retention", 1, 720),
    # --- Uploads / security / branding ---
    # Ceiling lowered from 5 GiB to 104 MB, which is what the stack can actually
    # accept. `client_max_body_size 110m` in docker/frontend/nginx.conf and
    # Traefik's maxRequestBodyBytes both cap the SAME request, so any value
    # above ~110 MB was accepted by this control and then 413'd at the edge -
    # an admin-facing setting whose upper range did nothing but produce failed
    # uploads. Raising it for real means changing the proxy layers too, which
    # is a host step, not a runtime knob (audit 2026-07-30).
    Tunable(K.MAX_DIRECT_UPLOAD_BYTES, "MAX_DIRECT_UPLOAD_BYTES", "int", "uploads", 1_000_000, 104_857_600),
    # --- Downloads ---
    # Signed-url TTL: 30s floor, 1h ceiling. The URL is an UNgated, transferable
    # bearer of the file bytes for its whole lifetime (audit #3), so the ceiling
    # is capped at 1h - enough for browser native-resume of an interrupted
    # download, without turning a leaked/forwarded URL into a day-long key. (Was
    # 24h.) Shorter still is better if you don't need resume.
    Tunable(K.DOWNLOAD_SIGNED_URL_TTL_SEC, "DOWNLOAD_SIGNED_URL_TTL_SEC", "int", "downloads", 30, 3600),
    # Resume credit: 1h floor, 7d ceiling. A ranged continuation inside this
    # window, from the user who already paid for the download, is not charged
    # again - which is what makes the desktop client's pause/resume free. Lower
    # it to tighten the budget; it can never grant anything to a caller with no
    # prior counted download of that file.
    Tunable(K.DOWNLOAD_RESUME_CREDIT_HOURS, "DOWNLOAD_RESUME_CREDIT_HOURS", "int", "downloads", 1, 168),
    # --- Updates ---
    # Postpone-update drain cap: 1 min floor, 24h ceiling. After this the
    # deferred update applies even if transfers haven't fully drained.
    Tunable(K.UPDATES_DRAIN_MAX_WAIT_MIN, "UPDATES_DRAIN_MAX_WAIT_MIN", "int", "updates", 1, 1440),
    Tunable(K.HIBP_ENABLED, "HIBP_ENABLED", "bool", "security"),
    Tunable(K.APP_NAME, "APP_NAME", "str", "branding"),
    # --- Storage / low-disk degradation (≤ 1 TiB byte ceiling) ---
    Tunable(K.STORAGE_LOW_THRESHOLD_PERCENT, "STORAGE_LOW_THRESHOLD_PERCENT", "int", "storage", 0, 50),
    Tunable(K.STORAGE_LOW_THRESHOLD_BYTES, "STORAGE_LOW_THRESHOLD_BYTES", "int", "storage", 0, 1_099_511_627_776),
    # --- Anomaly detection (heuristic; alerts by default, auto-block opt-in) ---
    Tunable(K.ANOMALY_ENABLED, "ANOMALY_ENABLED", "bool", "anomaly"),
    Tunable(K.ANOMALY_MASS_DOWNLOAD_THRESHOLD, "ANOMALY_MASS_DOWNLOAD_THRESHOLD", "int", "anomaly", 1, 100_000),
    Tunable(K.ANOMALY_MULTI_NETWORK_THRESHOLD, "ANOMALY_MULTI_NETWORK_THRESHOLD", "int", "anomaly", 2, 1000),
    Tunable(K.ANOMALY_LOGIN_FAILURE_THRESHOLD, "ANOMALY_LOGIN_FAILURE_THRESHOLD", "int", "anomaly", 1, 100_000),
    # --- Error alerting (email admins on server errors) ---
    Tunable(K.ERROR_ALERT_COOLDOWN_MINUTES, "ERROR_ALERT_COOLDOWN_MINUTES", "int", "error_alert", 1, 1440),
    Tunable(K.ERROR_ALERT_MAX_PER_HOUR, "ERROR_ALERT_MAX_PER_HOUR", "int", "error_alert", 1, 1000),
    Tunable(K.ERROR_LOG_SCAN_CAPTURE_PER_MIN, "ERROR_LOG_SCAN_CAPTURE_PER_MIN", "int", "error_alert", 10, 12000),
    # --- Scan guard (auto-block scanning sources; ships disabled) ---
    # `max_block_minutes` is clamped to 30 days on purpose: there is deliberately
    # no permanent block at any level, so every mistake self-heals unattended.
    Tunable(K.SCAN_GUARD_THRESHOLD, "SCAN_GUARD_THRESHOLD", "int", "scan_guard", 1, 1000),
    Tunable(K.SCAN_GUARD_WINDOW_SEC, "SCAN_GUARD_WINDOW_SEC", "int", "scan_guard", 30, 86400),
    Tunable(K.SCAN_GUARD_BLOCK_MINUTES, "SCAN_GUARD_BLOCK_MINUTES", "int", "scan_guard", 1, 43200),
    Tunable(K.SCAN_GUARD_MAX_BLOCK_MINUTES, "SCAN_GUARD_MAX_BLOCK_MINUTES", "int", "scan_guard", 1, 43200),
    Tunable(K.SCAN_GUARD_MIN_DISTINCT_PATHS, "SCAN_GUARD_MIN_DISTINCT_PATHS", "int", "scan_guard", 1, 500),
    Tunable(K.SCAN_GUARD_NETWORK_THRESHOLD, "SCAN_GUARD_NETWORK_THRESHOLD", "int", "scan_guard", 2, 254),
    Tunable(K.SCAN_GUARD_NETWORK_LOOKBACK_HOURS, "SCAN_GUARD_NETWORK_LOOKBACK_HOURS", "int", "scan_guard", 1, 8760),
    Tunable(K.SCAN_GUARD_MAX_NEW_BLOCKS_PER_MIN, "SCAN_GUARD_MAX_NEW_BLOCKS_PER_MIN", "int", "scan_guard", 1, 10000),
    Tunable(K.SCAN_GUARD_NETWORK_PREFIX_V6, "SCAN_GUARD_NETWORK_PREFIX_V6", "int", "scan_guard", 56, 128),
]

BY_KEY: dict[str, Tunable] = {t.key: t for t in TUNABLES}


def env_default(spec: Tunable):
    return getattr(_env, spec.env_attr)


def _clamp_int(value: int, spec: Tunable) -> int:
    if spec.min is not None and value < spec.min:
        return spec.min
    if spec.max is not None and value > spec.max:
        return spec.max
    return value


def effective(db: Session, key: str):
    """Live effective value for `key`: kv override (clamped) or env default."""
    spec = BY_KEY[key]
    default = env_default(spec)
    if spec.kind == "bool":
        return settings_svc.get_bool(db, key, default=bool(default))
    if spec.kind == "int":
        return _clamp_int(settings_svc.get_int(db, key, default=int(default)), spec)
    # str
    raw = settings_svc.get(db, key)
    return raw if raw not in (None, "") else str(default)


def coerce_for_store(spec: Tunable, value) -> str:
    """Validate + normalise an incoming value to its stored string form.
    Raises ValueError on a bad type or out-of-bounds int."""
    if spec.kind == "bool":
        if isinstance(value, bool):
            return "true" if value else "false"
        raise ValueError(f"{spec.key} expects a boolean")
    if spec.kind == "int":
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{spec.key} expects an integer") from None
        if spec.min is not None and n < spec.min:
            raise ValueError(f"{spec.key} must be >= {spec.min}")
        if spec.max is not None and n > spec.max:
            raise ValueError(f"{spec.key} must be <= {spec.max}")
        return str(n)
    # str
    s = str(value)
    if len(s) > 512:
        raise ValueError(f"{spec.key} is too long (max 512 chars)")
    return s
