"""TOTP per-user state. One-to-one with users.

The base32 secret is encrypted at rest with Fernet under a key HKDF-derived
from JWT_SECRET (see utils/crypto.py:encrypt_totp_secret).

`enabled_at` distinguishes "setup started but not confirmed" (NULL) from
"actively used" (timestamp). `last_used_counter` is the most recent TOTP
counter (Unix-time / 30 truncated) we accepted, used to refuse replay within
the validity window.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base

if TYPE_CHECKING:
    from .user import User


class UserTOTP(Base):
    __tablename__ = "user_totp"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    secret_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    last_used_counter: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")

    user: Mapped["User"] = relationship("User", back_populates="totp")
