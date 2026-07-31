"""Runtime settings store.

Generic key/value over MariaDB with optional Fernet-at-rest. Phase 9
used this for OIDC configuration; Phase 10 moved OIDC into a dedicated
``oidc_providers`` table, so right now this module is generic plumbing
that future settings can use.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlalchemy.orm import Session

from ..models.app_setting import AppSetting
from ..models.audit_log import AuditEventType
from ..models.user import User
from ..utils.crypto import decrypt_setting, encrypt_setting
from ..utils.timeutil import utc_now
from .audit import record_audit_event

logger = logging.getLogger("fileheron.settings")


# Keys we store. OIDC moved to its own table in Phase 10; the only
# remaining kv keys today are the API-token policy (post-Phase 10).
class Keys:
    # Set once, when the first admin is created. Never cleared: it is what
    # keeps the anonymous /setup wizard from re-opening if the instance ever
    # reaches zero enabled admins (audit 2026-07-30).
    SETUP_COMPLETED_AT = "setup.completed_at"
    API_TOKEN_POLICY_MODE = "api_token.policy_mode"
    API_TOKEN_ALLOWED_USERS = "api_token.allowed_user_ids"   # JSON list[int]
    API_TOKEN_ALLOWED_GROUPS = "api_token.allowed_group_ids"  # JSON list[int]
    PUBLIC_LINK_POLICY_MODE = "public_link.policy_mode"
    PUBLIC_LINK_ALLOWED_USERS = "public_link.allowed_user_ids"   # JSON list[int]
    PUBLIC_LINK_ALLOWED_GROUPS = "public_link.allowed_group_ids"  # JSON list[int]
    # SMTP - admin-editable; password is the only encrypted field.
    SMTP_HOST = "smtp.host"
    SMTP_PORT = "smtp.port"
    SMTP_USER = "smtp.user"
    SMTP_PASSWORD = "smtp.password"           # encrypted at rest
    SMTP_FROM_EMAIL = "smtp.from_email"
    SMTP_FROM_NAME = "smtp.from_name"
    SMTP_TLS_MODE = "smtp.tls_mode"           # 'implicit' | 'starttls' | 'none'
    SMTP_HELO_HOSTNAME = "smtp.helo_hostname"  # EHLO/HELO name; plaintext
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
    # addition to the uploader. Email is not sent - admin plaintext
    # email isn't stored.
    QUARANTINE_NOTIFY_ADMINS = "quarantine.notify_admins"  # boolean: 'true' / 'false'
    # Default state for the per-share "Notify recipient(s)" checkbox on
    # the create-share form. When the sender doesn't override the field,
    # this kv decides whether `share_created` notifications fan out.
    SHARE_NOTIFY_RECIPIENTS_DEFAULT = "share.notify_recipients_default"  # boolean
    # Share-approval workflow (v1.24.0). All admin-tunable, read live via
    # services/share_approval.py. The approver set reuses the policy_gate shape
    # (mode + additive user/group allowlist; admin always passes).
    SHARE_APPROVAL_ENABLED = "share_approval.enabled"            # boolean master switch
    SHARE_APPROVAL_APPROVER_MODE = "share_approval.approver_mode"  # admins_only | employees_admins
    SHARE_APPROVAL_APPROVER_USERS = "share_approval.approver_user_ids"   # JSON list[int]
    SHARE_APPROVAL_APPROVER_GROUPS = "share_approval.approver_group_ids"  # JSON list[int]
    SHARE_APPROVAL_SCOPE = "share_approval.scope"                # outbound | all | outbound_to_clients
    SHARE_APPROVAL_EXEMPT_APPROVERS = "share_approval.exempt_approvers"   # boolean (default true)
    SHARE_APPROVAL_ALLOW_CONTENT_REVIEW = "share_approval.allow_content_review"  # boolean (default true)
    # Global on/off for in-browser file preview (v1.23.0). Default true. When
    # false the SPA hides Preview buttons AND the preview endpoints refuse with
    # 403 (server-enforced, not just UI-hidden). See services/preview.py.
    FILE_PREVIEW_ENABLED = "file_preview.enabled"  # boolean: 'true' / 'false'
    # Email-change policy (v1.13.0). All three are admin-tunable and read
    # live; see services/email_change.py.
    #  - verification_mode: how a change is confirmed -
    #      'immediate'   = apply at once, no token (admin-trusted);
    #      'verify_new'  = pending; confirm via the NEW address (default);
    #      'verify_both' = pending; confirm via BOTH old and new addresses.
    #  - self_service: when true, non-admins may change their own email from
    #      the Account page. Off ⇒ admin-only ("deactivate email change for
    #      users"). Default off.
    #  - oidc_mode: what happens to an OIDC binding on email change -
    #      'reset_setpw' = unlink + mail a set-password link (default);
    #      'reset_only'  = unlink only;
    #      'keep'        = leave the binding intact.
    EMAIL_CHANGE_VERIFICATION_MODE = "email_change.verification_mode"
    EMAIL_CHANGE_SELF_SERVICE = "email_change.self_service"  # boolean
    EMAIL_CHANGE_OIDC_MODE = "email_change.oidc_mode"
    # Phase 5 self-update: the full GitHub-compatible releases endpoint a fork
    # operator can repoint at their own repo. Cadence lives in the cron
    # scheduler (`cron.release_check.*`) since v1.28.0; the `updates.check_mode`
    # key that used to hold it was left behind with nothing reading it, and its
    # documentation outlived the code by four releases (audit 2026-07-30).
    UPDATES_API_URL = "updates.api_url"        # plain string
    # Inbound mailbox / IMAP fetch (v1.27.0). Admin-editable; password is the
    # only encrypted field (mirrors SMTP). Read via services/imap_config.py.
    IMAP_ENABLED = "imap.enabled"              # boolean master switch (default false)
    # When true (default), IMAP borrows the SMTP username/password (and host as a
    # default) so the admin doesn't re-enter the outgoing-email login.
    IMAP_USE_SMTP_CREDENTIALS = "imap.use_smtp_credentials"  # boolean
    IMAP_HOST = "imap.host"
    IMAP_PORT = "imap.port"
    IMAP_USER = "imap.user"
    IMAP_PASSWORD = "imap.password"            # encrypted at rest
    IMAP_TLS_MODE = "imap.tls_mode"            # 'implicit' | 'starttls' | 'none'
    IMAP_MAILBOX = "imap.mailbox"              # source folder, default 'INBOX'
    # What to do with a message on the server after ingest:
    #   'mark_read' (default) | 'untouched' | 'move' | 'delete'
    IMAP_POST_FETCH_ACTION = "imap.post_fetch_action"
    IMAP_MOVE_FOLDER = "imap.move_folder"      # target folder when action='move'
    IMAP_NOTIFY_MODE = "imap.notify_mode"      # 'off' (default) | 'human' | 'all'
    # Cron state (set with actor=None; not user-facing settings).
    IMAP_LAST_POLL_AT = "imap.last_poll_at"
    IMAP_LAST_SUCCESS_AT = "imap.last_success_at"
    IMAP_LAST_UID = "imap.last_uid"
    IMAP_UIDVALIDITY = "imap.uidvalidity"
    # Registry tunable (overlays config.Settings env; see settings_registry).
    IMAP_MESSAGE_RETENTION_DAYS = "retention.inbound_message_days"

    # --- Advanced runtime-tunable knobs (registry-driven, see
    # services/settings_registry.py). Each overlays the matching env
    # default in config.Settings; read live via settings_registry.effective.
    ACCESS_TOKEN_EXPIRE_MINUTES = "auth.access_token_expire_minutes"
    REFRESH_TOKEN_EXPIRE_DAYS = "auth.refresh_token_expire_days"
    MAX_ACTIVE_SESSIONS_PER_USER = "auth.max_active_sessions_per_user"
    RATE_LIMIT_LOGIN = "rate_limit.login"
    RATE_LIMIT_REGISTER = "rate_limit.register"
    LOGIN_RATE_WINDOW_SEC = "rate_limit.login_window_sec"
    LOCKOUT_THRESHOLD = "rate_limit.lockout_threshold"
    LOCKOUT_DURATION_MIN = "rate_limit.lockout_duration_min"
    PUBLIC_LINK_PASSWORD_RATE_LIMIT = "public_link.password_rate_limit"
    PUBLIC_LINK_PASSWORD_WINDOW_SEC = "public_link.password_window_sec"
    PUBLIC_LINK_LOCKOUT_SEC = "public_link.lockout_sec"
    REFRESH_TOKEN_RETENTION_DAYS = "retention.refresh_token_days"
    INVITE_RETENTION_DAYS = "retention.invite_days"
    AUDIT_LOG_RETENTION_DAYS = "retention.audit_log_days"
    DOWNLOAD_LOG_RETENTION_DAYS = "retention.download_log_days"
    EMAIL_LOG_RETENTION_DAYS = "retention.email_log_days"
    LOGIN_ATTEMPT_RETENTION_DAYS = "retention.login_attempt_days"
    WEBHOOK_DELIVERY_RETENTION_DAYS = "retention.webhook_delivery_days"
    NOTIFICATION_READ_RETENTION_DAYS = "retention.notification_read_days"
    QUARANTINE_PURGE_AFTER_DAYS = "retention.quarantine_purge_days"
    ORPHAN_RECLAIM_AFTER_DAYS = "retention.orphan_reclaim_days"
    TUS_UPLOAD_ABANDONED_AFTER_HOURS = "retention.tus_abandoned_hours"
    UPLOAD_STALE_AFTER_HOURS = "retention.upload_stale_hours"
    MAX_DIRECT_UPLOAD_BYTES = "uploads.max_direct_bytes"
    DOWNLOAD_SIGNED_URL_TTL_SEC = "downloads.signed_url_ttl_sec"
    HIBP_ENABLED = "security.hibp_enabled"
    APP_NAME = "branding.app_name"
    # Site branding (v1.31.0): an admin-uploaded logo + where it shows + an
    # optional link. Logo bytes live in the storage backend; these keep the
    # pointer + display surfaces.
    BRANDING_LOGO_LOCATOR = "branding.logo_locator"
    BRANDING_LOGO_FILENAME = "branding.logo_filename"
    BRANDING_LOGO_CONTENT_TYPE = "branding.logo_content_type"
    # Header-sized PNG rendition for the desktop client (Tk PhotoImage needs
    # PNG + can't resize). Generated at upload time from the original.
    BRANDING_LOGO_PNG_LOCATOR = "branding.logo_png_locator"
    BRANDING_SHOW_HEADER = "branding.show_header"   # boolean
    BRANDING_SHOW_LOGIN = "branding.show_login"     # boolean
    BRANDING_SHOW_PUBLIC = "branding.show_public"   # boolean
    BRANDING_SHOW_EMAIL = "branding.show_email"     # boolean
    BRANDING_SHOW_CLIENT = "branding.show_client"   # boolean (desktop client)
    BRANDING_LINK_URL = "branding.link_url"         # optional URL the logo links to
    # Legal pages (v1.31.0): admin-editable Imprint + Privacy, per-locale
    # markdown, each independently enabled. Shown as footer links on every page.
    LEGAL_IMPRINT_ENABLED = "legal.imprint_enabled"  # boolean
    LEGAL_IMPRINT_EN = "legal.imprint_en"            # markdown
    LEGAL_IMPRINT_DE = "legal.imprint_de"            # markdown
    LEGAL_PRIVACY_ENABLED = "legal.privacy_enabled"  # boolean
    LEGAL_PRIVACY_EN = "legal.privacy_en"            # markdown
    LEGAL_PRIVACY_DE = "legal.privacy_de"            # markdown
    # Low-disk degradation. The two thresholds are registry tunables (overlay
    # the config.Settings env defaults); `critical_low` is a plain runtime flag
    # the disk_check cron flips (not a tunable - never user-set).
    STORAGE_LOW_THRESHOLD_PERCENT = "storage.low_threshold_percent"
    STORAGE_LOW_THRESHOLD_BYTES = "storage.low_threshold_bytes"
    STORAGE_CRITICAL_LOW = "storage.critical_low"  # boolean flag, cron-managed
    # Anomaly detection (v1.20.0). Thresholds are registry tunables; enabled is
    # the master switch.
    ANOMALY_ENABLED = "anomaly.enabled"
    ANOMALY_MASS_DOWNLOAD_THRESHOLD = "anomaly.mass_download_threshold"
    ANOMALY_MULTI_NETWORK_THRESHOLD = "anomaly.multi_network_threshold"
    ANOMALY_LOGIN_FAILURE_THRESHOLD = "anomaly.login_failure_threshold"
    # Maintenance mode (v1.34.0). When enabled, NEW file transfers (uploads +
    # downloads, incl. public links / ZIP / preview) are refused with 503 while
    # in-progress ones finish; the rest of the app stays usable. Toggled manually
    # by an admin or automatically by the postpone-update flow. `pending_update`
    # holds the deferred self-update the drain worker fires once transfers drain.
    MAINTENANCE_ENABLED = "maintenance.enabled"          # boolean
    MAINTENANCE_MESSAGE = "maintenance.message"          # optional banner text
    MAINTENANCE_PENDING_UPDATE = "maintenance.pending_update"  # JSON or absent
    # Registry tunable: how long the postpone flow waits for transfers to drain
    # before applying the update anyway.
    UPDATES_DRAIN_MAX_WAIT_MIN = "updates.drain_max_wait_min"
    # Error alerting (email admins on server errors). Master switch + the HTTP-5xx
    # source toggle + recipient targeting are simple kv; the cooldown + hourly cap
    # are registry tunables (see settings_registry). The cron/worker source is NOT
    # here - it's a per-task `cron.<name>.alert_on_failure` flag on the Scheduled
    # tasks page. See services/error_alert.py.
    ERROR_ALERT_ENABLED = "error_alert.enabled"                  # boolean master switch (default false)
    ERROR_ALERT_SOURCE_HTTP_5XX = "error_alert.source_http_5xx"  # boolean (default true)
    ERROR_ALERT_SOURCE_HTTP_4XX = "error_alert.source_http_4xx"  # boolean (default false) - email captured 4xx too
    ERROR_ALERT_RECIPIENTS_MODE = "error_alert.recipients_mode"  # 'admins' | 'custom' (default 'admins')
    ERROR_ALERT_CUSTOM_RECIPIENTS = "error_alert.custom_recipients"  # CSV of email addresses
    ERROR_ALERT_COOLDOWN_MINUTES = "error_alert.cooldown_minutes"    # registry tunable (int)
    ERROR_ALERT_MAX_PER_HOUR = "error_alert.max_per_hour"            # registry tunable (int)
    # Error LOG (persist to the error_log table + the admin viewer). Decoupled
    # from the alert switch above: logging captures every qualifying error; the
    # cooldown/cap only throttle emails. See services/error_log.py.
    ERROR_LOG_ENABLED = "error_log.enabled"            # boolean (default true) - persist 5xx + cron failures
    ERROR_LOG_CAPTURE_4XX = "error_log.capture_4xx"    # boolean (default false) - also persist allowlisted 4xx
    ERROR_LOG_4XX_CODES = "error_log.http_4xx_codes"   # CSV of HTTP status codes (e.g. "429,409")
    ERROR_LOG_RETENTION_DAYS = "error_log.retention_days"  # registry tunable (int; 0 disables prune)
    PUBLIC_LINK_ATTEMPT_RETENTION_DAYS = "retention.public_link_attempt_days"  # registry tunable
    ERROR_LOG_SCAN_CAPTURE_PER_MIN = "error_log.scan_capture_per_min"  # registry tunable (int; 4xx capture rate/min)


_ENCRYPTED_KEYS: set[str] = {Keys.SMTP_PASSWORD, Keys.IMAP_PASSWORD}




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
            # re-encrypting). Treat as missing rather than crashing -
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
            updated_at=utc_now(),
            updated_by_id=actor.id if actor else None,
        )
        db.add(row)
    else:
        row.value = stored
        row.is_encrypted = encrypted
        row.updated_at = utc_now()
        row.updated_by_id = actor.id if actor else None
    db.flush()


def audit_settings_change(
    db: Session,
    *,
    actor: User | None,
    changed_keys: Iterable[str],
    request=None,
) -> None:
    """Single audit row covering the entire PUT - captures which keys
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


def get_int(db: Session, key: str, default: int) -> int:
    """Read an integer kv setting, falling back to `default` when the row
    is missing or the stored value isn't a valid int. Mirrors get_bool."""
    raw = get(db, key)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default
