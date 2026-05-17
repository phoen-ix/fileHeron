"""Runtime settings store.

Generic key/value over MariaDB with optional Fernet-at-rest. Phase 9
used this for OIDC configuration; Phase 10 moved OIDC into a dedicated
``oidc_providers`` table, so right now this module is generic plumbing
that future settings can use.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from ..models.app_setting import AppSetting
from ..models.audit_log import AuditEventType
from ..models.user import User
from ..utils.crypto import decrypt_setting, encrypt_setting
from .audit import record_audit_event

logger = logging.getLogger("fileheron.settings")


# Keys we store. OIDC moved to its own table in Phase 10; the only
# remaining kv keys today are the API-token policy (post-Phase 10).
class Keys:
    API_TOKEN_POLICY_MODE = "api_token.policy_mode"
    API_TOKEN_ALLOWED_USERS = "api_token.allowed_user_ids"   # JSON list[int]
    API_TOKEN_ALLOWED_GROUPS = "api_token.allowed_group_ids"  # JSON list[int]
    PUBLIC_LINK_POLICY_MODE = "public_link.policy_mode"
    PUBLIC_LINK_ALLOWED_USERS = "public_link.allowed_user_ids"   # JSON list[int]
    PUBLIC_LINK_ALLOWED_GROUPS = "public_link.allowed_group_ids"  # JSON list[int]
    # SMTP — admin-editable; password is the only encrypted field.
    SMTP_HOST = "smtp.host"
    SMTP_PORT = "smtp.port"
    SMTP_USER = "smtp.user"
    SMTP_PASSWORD = "smtp.password"           # encrypted at rest
    SMTP_FROM_EMAIL = "smtp.from_email"
    SMTP_FROM_NAME = "smtp.from_name"
    SMTP_TLS_MODE = "smtp.tls_mode"           # 'implicit' | 'starttls' | 'none'
    HOME_PAGE_ENABLED = "home_page.enabled"   # boolean: 'true' / 'false'
    # Login-page MOTD banner. When MOTD_ENABLED is true and MOTD_TEXT is
    # non-empty, /api/config-public surfaces both so the anonymous SPA
    # login view renders a notice. Plain text, no Markdown (kept simple
    # to avoid an HTML sanitizer dependency on the login surface).
    MOTD_ENABLED = "motd.enabled"             # boolean
    MOTD_TEXT = "motd.text"                   # plaintext, max ~500 chars
    # Admin-editable site URL. When set, overrides the ``APP_URL`` env
    # var for every user-facing URL builder (emails, public links,
    # notification link_url). WebAuthn RP origin + OIDC redirect URI
    # stay on env (security-sensitive). Read via
    # ``services/site.py::get_site_url``.
    SITE_URL = "site.url"
    # Admin-editable site-wide display timezone. IANA name
    # (e.g. "Europe/Vienna"). Default "UTC" when unset. Used by every
    # human-facing timestamp render: SPA via /api/config-public,
    # email templates via the dt_locale Jinja filter. Read via
    # ``services/site.py::get_site_timezone``.
    SITE_TIMEZONE = "site.timezone"
    # 2FA enforcement policy. When neither key is set the env knob
    # `REQUIRE_2FA` is the source of truth.
    TWOFA_REQUIRED_ROLES = "twofa.required_roles"        # JSON list[str]
    TWOFA_REQUIRED_GROUPS = "twofa.required_group_ids"   # JSON list[int]
    # When true, ClamAV-detected infections fan out an in-app
    # `file_quarantined` notification to every non-disabled admin in
    # addition to the uploader. Email is not sent — admin plaintext
    # email isn't stored.
    QUARANTINE_NOTIFY_ADMINS = "quarantine.notify_admins"  # boolean: 'true' / 'false'
    # Default state for the per-share "Notify recipient(s)" checkbox on
    # the create-share form. When the sender doesn't override the field,
    # this kv decides whether `share_created` notifications fan out.
    SHARE_NOTIFY_RECIPIENTS_DEFAULT = "share.notify_recipients_default"  # boolean
    # Phase 5 self-update: configurable update-check. URL is the full
    # GitHub-compatible releases endpoint a fork operator can repoint
    # at their own repo; mode is 'auto' (poll once every 24h) or
    # 'manual' (only when admin clicks Check now).
    UPDATES_API_URL = "updates.api_url"        # plain string
    UPDATES_CHECK_MODE = "updates.check_mode"  # 'auto' | 'manual'


_ENCRYPTED_KEYS: set[str] = {Keys.SMTP_PASSWORD}


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def get(db: Session, key: str) -> str | None:
    """Return the stored value for `key`, or None if no override exists.
    Decrypts if the row is marked encrypted."""
    row = db.query(AppSetting).filter(AppSetting.key == key).one_or_none()
    if row is None:
        return None
    if row.is_encrypted:
        try:
            return decrypt_setting(row.value)
        except Exception:
            # Decryption failure (e.g. JWT_SECRET was rotated without
            # re-encrypting). Treat as missing rather than crashing —
            # the env fallback will kick in.
            logger.warning(
                "settings.get: decryption failed for key=%s", key
            )
            return None
    return row.value


def set_value(
    db: Session,
    *,
    key: str,
    value: str | None,
    actor: User | None,
    request=None,
) -> None:
    """Upsert (or delete if `value` is None). Caller commits."""
    row = db.query(AppSetting).filter(AppSetting.key == key).one_or_none()
    if value is None:
        if row is not None:
            db.delete(row)
            db.flush()
        return

    encrypted = key in _ENCRYPTED_KEYS
    stored = encrypt_setting(value) if encrypted else value
    if row is None:
        row = AppSetting(
            key=key,
            value=stored,
            is_encrypted=encrypted,
            updated_at=_utcnow(),
            updated_by_id=actor.id if actor else None,
        )
        db.add(row)
    else:
        row.value = stored
        row.is_encrypted = encrypted
        row.updated_at = _utcnow()
        row.updated_by_id = actor.id if actor else None
    db.flush()


def audit_settings_change(
    db: Session,
    *,
    actor: User | None,
    changed_keys: Iterable[str],
    request=None,
) -> None:
    """Single audit row covering the entire PUT — captures which keys
    moved without ever logging the values."""
    record_audit_event(
        db,
        event_type=AuditEventType.settings_changed,
        actor_user_id=actor.id if actor else None,
        target_type="settings",
        target_id=None,
        metadata={"keys": sorted(set(changed_keys))},
        request=request,
    )




def get_bool(db: Session, key: str, default: bool = False) -> bool:
    """Read a boolean kv setting. Accepts the canonical lowercase forms
    'true'/'false' plus the truthy/falsy variants '1'/'0' that admins
    sometimes type into curl. Returns `default` when the row is missing
    or the value can't be parsed."""
    raw = get(db, key)
    if raw is None:
        return default
    if raw.lower() in ("true", "1", "yes", "on"):
        return True
    if raw.lower() in ("false", "0", "no", "off"):
        return False
    return default
