"""Invite tokens: single-use, 24h expiry, addressed to a specific
plaintext ``email`` so the consumer can be matched by the registration
form."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Enum as SAEnum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from .user import UserRole

if TYPE_CHECKING:
    from .user import User


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


class InviteToken(Base):
    __tablename__ = "invite_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # Plaintext invitee email. Indexed so consume-by-email is fast.
    email: Mapped[str] = mapped_column(String(254), index=True, nullable=False)
    target_role: Mapped[UserRole] = mapped_column(SAEnum(UserRole, native_enum=False, length=20), nullable=False)

    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped["User"] = relationship("User", back_populates="invites_created", foreign_keys=[created_by_id])

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    used_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Phase post-10: groups the invitee is auto-added to on invite consume.
    # NULL or [] = none. Group IDs are validated at invite time so a deleted
    # group between invite and consume is silently skipped (defensive).
    initial_group_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
