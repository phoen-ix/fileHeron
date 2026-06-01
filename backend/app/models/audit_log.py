"""Append-only audit log. Every privileged or security-relevant action emits
exactly one row via `services/audit.py:record_audit_event(...)`.

The `event_type` column uses a string enum so new event types can be added
without migrating the column type. The `target_*` columns are loose strings
because targets span tables (users, shares, files, public_links, ...).
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class AuditEventType(str, enum.Enum):
    # Phase 1a events
    user_registered = "user_registered"
    user_created_by_admin = "user_created_by_admin"          # admin created a user directly (no invite)
    email_verified = "email_verified"
    invite_created = "invite_created"
    invite_consumed = "invite_consumed"
    invite_revoked = "invite_revoked"
    invite_purged = "invite_purged"                          # v1.1.5 cron
    login_success = "login_success"
    login_failure = "login_failure"
    logout = "logout"
    refresh_token_rotated = "refresh_token_rotated"
    refresh_token_reused = "refresh_token_reused"
    password_changed = "password_changed"
    password_reset_requested = "password_reset_requested"
    password_reset_consumed = "password_reset_consumed"
    admin_bootstrapped = "admin_bootstrapped"

    # Reserved for later phases (registered here for Alembic stability)
    totp_enabled = "totp_enabled"                         # Phase 1b
    totp_disabled = "totp_disabled"                        # Phase 1b
    recovery_code_used = "recovery_code_used"              # Phase 1b
    account_locked = "account_locked"                      # Phase 1b
    rate_limited = "rate_limited"                          # Phase 1b
    share_created = "share_created"                        # Phase 3a/4
    share_revoked = "share_revoked"                        # Phase 3a/4
    share_expired = "share_expired"                        # Phase 4
    file_downloaded = "file_downloaded"                    # Phase 3a
    file_deleted = "file_deleted"                          # Phase 3a/4
    file_quarantined = "file_quarantined"                  # Phase 5
    file_quarantine_released = "file_quarantine_released"  # post-Phase 10
    file_quarantine_purged = "file_quarantine_purged"      # post-Phase 10
    quarantine_policy_changed = "quarantine_policy_changed"  # post-Phase 10
    av_reload_triggered = "av_reload_triggered"            # v1.1.6
    public_link_created = "public_link_created"            # Phase 5
    public_link_revoked = "public_link_revoked"            # Phase 5
    public_link_consumed = "public_link_consumed"          # Phase 5
    role_changed = "role_changed"                          # Phase 6b
    user_disabled = "user_disabled"                        # Phase 6b
    user_erased = "user_erased"                            # Phase 6b
    oidc_linked = "oidc_linked"                            # Phase 7
    oidc_unlinked = "oidc_unlinked"                        # Phase 10
    oidc_provider_created = "oidc_provider_created"        # Phase 10
    oidc_provider_updated = "oidc_provider_updated"        # Phase 10
    oidc_provider_deleted = "oidc_provider_deleted"        # Phase 10
    api_token_created = "api_token_created"                # Phase 3a
    api_token_revoked = "api_token_revoked"                # Phase 3a
    api_token_disabled = "api_token_disabled"              # post-Phase 10
    api_token_reactivated = "api_token_reactivated"        # post-Phase 10
    api_token_admin_revoked = "api_token_admin_revoked"    # post-Phase 10
    api_token_admin_created = "api_token_admin_created"    # post-Phase 10
    api_policy_changed = "api_policy_changed"              # post-Phase 10
    public_link_policy_changed = "public_link_policy_changed"  # post-Phase 10
    share_expiry_updated = "share_expiry_updated"            # post-Phase 10
    share_limit_updated = "share_limit_updated"              # v1.1.0 (per-share download budget)
    refresh_token_evicted = "refresh_token_evicted"          # post-Phase 10 (session cap)
    smtp_config_changed = "smtp_config_changed"              # post-Phase 10
    home_page_toggled = "home_page_toggled"                  # post-Phase 10
    motd_changed = "motd_changed"                            # login-page banner
    share_defaults_policy_changed = "share_defaults_policy_changed"  # post-Phase 10
    site_url_changed = "site_url_changed"                    # post-Phase 10
    site_timezone_changed = "site_timezone_changed"          # v1.1.3
    twofa_policy_changed = "twofa_policy_changed"            # post-Phase 10
    file_finalized = "file_finalized"                        # post-Phase 10 (was miscategorised as share_created)
    group_created = "group_created"                        # Phase 4
    group_updated = "group_updated"                        # Phase 4
    group_deleted = "group_deleted"                        # Phase 4
    group_member_added = "group_member_added"              # Phase 4
    group_member_removed = "group_member_removed"          # Phase 4
    file_expired = "file_expired"                          # Phase 4
    settings_changed = "settings_changed"                  # Phase 9
    # Operational audit additions (2026-05-16):
    email_undeliverable = "email_undeliverable"            # SMTP 5xx → audit + admin alert (was silently swallowed)
    cron_failed = "cron_failed"                            # cron_tracker logs the failure to audit + admin alert
    cron_run_triggered = "cron_run_triggered"              # admin ran a cron on demand from /admin/system
    ops_alert_dispatched = "ops_alert_dispatched"          # per-admin in-app ops notification fired
    # Self-update flow (Phase 4). `update_triggered` and `rollback_triggered`
    # record the admin actor + target tag. The terminal events are written
    # after the backend polls the updater's job to completion.
    update_triggered = "update_triggered"
    update_completed = "update_completed"
    update_failed = "update_failed"
    rollback_triggered = "rollback_triggered"
    rollback_completed = "rollback_completed"
    rollback_failed = "rollback_failed"
    # Phase 5: admin-editable Updates settings (URL + check_mode).
    updates_settings_changed = "updates_settings_changed"
    # v1.5.1: cleanup_stale_uploads reaper — an upload abandoned in `uploading`
    # past retention.upload_stale_hours, and the share it left empty.
    file_upload_abandoned = "file_upload_abandoned"
    share_failed = "share_failed"


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


# In SQLite, BigInteger PRIMARY KEY does NOT autoincrement — only INTEGER PK
# does (via ROWID alias). Use a type variant so prod gets BIGINT and tests
# (SQLite) get INTEGER which DOES autoincrement.
_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=_utcnow, index=True)

    actor_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    extra: Mapped[dict | None] = mapped_column("metadata_json", JSON, nullable=True)
