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
from sqlalchemy import ForeignKey, Integer
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
