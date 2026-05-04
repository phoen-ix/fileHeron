"""User model. Core principles:

- Email is stored as plaintext in the indexed unique ``email`` column.
  Always lowercased + stripped on write (see ``utils/crypto.normalize_email``).
  This is the primary lookup key for login + every notification dispatch.
- Role + locale are Python Enums mapped to SQL ENUM columns.
- `oidc_subject` is populated in Phase 7 when the user logs in via OIDC.
- `quota_bytes` NULL means unlimited; admins set it in user-management UI.
- `created_by` records the inviter (NULL for the bootstrapped admin).
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, Enum as SAEnum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base

if TYPE_CHECKING:
    from .invite_token import InviteToken
    from .refresh_token import RefreshToken
    from .user_recovery_code import UserRecoveryCode
    from .user_totp import UserTOTP


class UserRole(str, enum.Enum):
    admin = "admin"
    employee = "employee"
    client = "client"


class Locale(str, enum.Enum):
    de = "de"
    en = "en"


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Email — plaintext, always lowercased + stripped on write.
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True, nullable=False)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)

    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, native_enum=False, length=20), nullable=False, default=UserRole.client
    )
    locale: Mapped[Locale] = mapped_column(
        SAEnum(Locale, native_enum=False, length=2), nullable=False, default=Locale.en
    )

    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Phase 1b: per-account lockout. failed_login_count is bumped on each
    # invalid-credentials failure; reaching the threshold sets locked_until.
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    # Used to dedupe lockout warning emails (6h window).
    lockout_email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=_utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    # Phase 7: populated when a user logs in via OIDC. Phase 10 makes
    # uniqueness composite with `oidc_provider_id` so two providers can
    # both have an "alice" subject without colliding.
    oidc_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Phase 10: which OIDC provider this user is linked to. NULL = no link.
    oidc_provider_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("oidc_providers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # NULL = unlimited
    quota_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Post-Phase 10: per-user post-login destination. Stores a route
    # name (e.g. "outbox") or NULL to use the system default.
    # Validated against `services/account_prefs.ALLOWED_LANDING_ROUTES`
    # in the PATCH endpoint.
    default_landing_page: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )

    # The inviter. NULL for the bootstrapped admin.
    created_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped["User | None"] = relationship("User", remote_side="User.id")

    # Backrefs
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan",
        foreign_keys="RefreshToken.user_id",
    )
    invites_created: Mapped[list["InviteToken"]] = relationship(
        "InviteToken", back_populates="created_by",
        foreign_keys="InviteToken.created_by_id",
    )
    totp: Mapped["UserTOTP | None"] = relationship(
        "UserTOTP", back_populates="user", uselist=False, cascade="all, delete-orphan",
    )
    recovery_codes: Mapped[list["UserRecoveryCode"]] = relationship(
        "UserRecoveryCode", back_populates="user", cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} role={self.role.value} email={self.email!r}>"
