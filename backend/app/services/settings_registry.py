"""Registry of runtime-tunable settings (".env → admin UI" migration).

Each `Tunable` maps an `app_settings` kv key to its `config.Settings`
env-default attribute, a type, a UI group, and (for ints) safe bounds.
`effective(db, key)` returns the live value: the kv override if present
(clamped to bounds), otherwise the env default. Reads are live per call —
no boot cache — so an admin change applies without a redeploy, exactly
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
    Tunable(K.LOGIN_ATTEMPT_RETENTION_DAYS, "LOGIN_ATTEMPT_RETENTION_DAYS", "int", "retention", 0, 3650),
    Tunable(K.NOTIFICATION_READ_RETENTION_DAYS, "NOTIFICATION_READ_RETENTION_DAYS", "int", "retention", 0, 3650),
    Tunable(K.QUARANTINE_PURGE_AFTER_DAYS, "QUARANTINE_PURGE_AFTER_DAYS", "int", "retention", 0, 3650),
    Tunable(K.ORPHAN_RECLAIM_AFTER_DAYS, "ORPHAN_RECLAIM_AFTER_DAYS", "int", "retention", 0, 3650),
    Tunable(K.TUS_UPLOAD_ABANDONED_AFTER_HOURS, "TUS_UPLOAD_ABANDONED_AFTER_HOURS", "int", "retention", 1, 8760),
    # --- Uploads / security / branding ---
    Tunable(K.MAX_DIRECT_UPLOAD_BYTES, "MAX_DIRECT_UPLOAD_BYTES", "int", "uploads", 1_000_000, 5_368_709_120),
    Tunable(K.HIBP_ENABLED, "HIBP_ENABLED", "bool", "security"),
    Tunable(K.APP_NAME, "APP_NAME", "str", "branding"),
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
