"""Per-user notification preferences.

One row per (user, category) pair. Channel ∈ off | email | in_app |
both. Absence of a row = default `both`.

Defaults are applied at read time (services/notification.py), so we
don't need to seed rows on user creation - keeps user creation cheap
and lets us add new categories without a backfill.
"""
from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from .notification import NotificationCategory

if TYPE_CHECKING:
    from .user import User


class NotificationChannel(str, enum.Enum):
    off = "off"
    email = "email"
    in_app = "in_app"
    both = "both"


class UserNotificationPreference(Base):
    __tablename__ = "user_notification_preferences"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    category: Mapped[NotificationCategory] = mapped_column(
        SAEnum(NotificationCategory, native_enum=False, length=40),
        primary_key=True,
    )
    channel: Mapped[NotificationChannel] = mapped_column(
        SAEnum(NotificationChannel, native_enum=False, length=10),
        nullable=False,
        default=NotificationChannel.both,
    )

    user: Mapped[User] = relationship("User", foreign_keys=[user_id])


class UserNotificationTopicPreference(Base):
    """A finer choice inside ONE category: a row per (user, category, topic).

    Only `ops_alert` has topics - its `reason`
    (services/admin_alerts.OPS_REASONS). One preference covered sixteen
    different alarms and notices, so "mail me when antivirus fails, not when an
    update starts" could not be said. A table of its own, so the per-category
    rows and every query on them stay as they were; a category row keeps
    working as the fallback for the topics without one
    (services/notification_prefs.effective_channel)."""

    __tablename__ = "user_notification_topic_preferences"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    category: Mapped[NotificationCategory] = mapped_column(
        SAEnum(NotificationCategory, native_enum=False, length=40),
        primary_key=True,
    )
    topic: Mapped[str] = mapped_column(String(40), primary_key=True)
    channel: Mapped[NotificationChannel] = mapped_column(
        SAEnum(NotificationChannel, native_enum=False, length=10),
        nullable=False,
    )
