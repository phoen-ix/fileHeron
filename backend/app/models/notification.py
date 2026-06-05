"""User-facing notifications.

A notification is the durable record that something happened that the
user should know about. Always written, regardless of how (or whether)
the email goes out — the UI bell reads from this table.

`payload_json` is intentionally schemaless: each category defines its
own keys. The renderer in services/notification.py knows how to turn
each category + payload into a UI line.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ..utils.timeutil import utc_now

# BIGINT does not autoincrement under SQLite; fall through to ROWID.
_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")

if TYPE_CHECKING:
    from .user import User


class NotificationCategory(str, enum.Enum):
    share_created = "share_created"
    # Fired when a share's owner adds more files to an already-active share
    # (opt-in per add). Stored as a plain string (non-native enum) → no
    # migration for the new value.
    share_files_added = "share_files_added"
    share_expiring = "share_expiring"
    public_link_downloaded = "public_link_downloaded"
    account_created = "account_created"
    # Kept for parity with the rest of the categories even though the actual
    # send goes via the direct `services/email.py::send_password_reset_email`
    # rather than `notification.dispatch`. Slug name matches the template
    # file `email/{locale}/reset_password.txt.j2`.
    reset_password = "reset_password"
    login_alert = "login_alert"
    # Security notice: an SSO (OIDC) identity was just linked to this
    # account (auto-linked on first verified-email sign-in). Lets a user
    # spot an unauthorised link. Stored as a plain string (non-native
    # enum) so no migration is needed for the new value.
    oidc_linked = "oidc_linked"
    file_quarantined = "file_quarantined"
    # Fired when the session cap (MAX_ACTIVE_SESSIONS_PER_USER) signs an
    # older idle session out on a new login — so the eviction isn't silent.
    # Stored as a plain string (non-native enum), so no migration for the
    # new value. Default channel in_app; users can opt into email.
    session_evicted = "session_evicted"
    # Ops alerts (admin-only). Single category, payload.reason discriminates
    # cron_failed / av_unhealthy / smtp_failing / dispatch_failed. In-app
    # only — admins don't have stored plaintext email in this design.
    ops_alert = "ops_alert"
    # Phase 5: fired when the release-check cron (or the on-demand button)
    # detects a new upstream release. Admin-only at the dispatch site;
    # default channel is `both` so admins get the email too without
    # opening the app.
    release_available = "release_available"


# Categories only ever dispatched to admins (the dispatch sites filter on
# role). Non-admins must not see preference toggles for them — they'd be inert.
ADMIN_ONLY_CATEGORIES = frozenset(
    {NotificationCategory.ops_alert, NotificationCategory.release_available}
)




class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # Bell list (user_id + created_at DESC) + the daily age-out cron.
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[NotificationCategory] = mapped_column(
        SAEnum(NotificationCategory, native_enum=False, length=40),
        nullable=False,
        index=True,
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    link_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, index=True
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(), nullable=True
    )

    user: Mapped[User] = relationship("User", foreign_keys=[user_id])
