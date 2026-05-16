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
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    JSON,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base

# BIGINT does not autoincrement under SQLite; fall through to ROWID.
_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")

if TYPE_CHECKING:
    from .user import User


class NotificationCategory(str, enum.Enum):
    share_created = "share_created"
    share_expiring = "share_expiring"
    public_link_downloaded = "public_link_downloaded"
    account_created = "account_created"
    # Kept for parity with the rest of the categories even though the actual
    # send goes via the direct `services/email.py::send_password_reset_email`
    # rather than `notification.dispatch`. Slug name matches the template
    # file `email/{locale}/reset_password.txt.j2`.
    reset_password = "reset_password"
    login_alert = "login_alert"
    file_quarantined = "file_quarantined"
    # Ops alerts (admin-only). Single category, payload.reason discriminates
    # cron_failed / av_unhealthy / smtp_failing / dispatch_failed. In-app
    # only — admins don't have stored plaintext email in this design.
    ops_alert = "ops_alert"


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


class Notification(Base):
    __tablename__ = "notifications"

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
        DateTime(), nullable=False, default=_utcnow, index=True
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(), nullable=True
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
