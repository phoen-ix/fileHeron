"""Programmatic-access API tokens.

Format on the wire: `fh_<8-hex-id>_<43-char-base64url>`. The 8-hex prefix
gives us a fast lookup column (`prefix`); the 43-char suffix is 32 bytes of
URL-safe random encoded base64 (no padding). The full secret is hashed
SHA-256 (high entropy → no Argon2 needed).

Auth flow:
- User: POST /api/account/api-tokens {name} → server returns plaintext ONCE.
- Backend stores: prefix, last4 (for display), secret_hash.
- API client: `Authorization: Bearer fh_<prefix>_<secret>`.
- Backend verify: split prefix vs secret; index lookup by prefix; constant-
  time compare against secret_hash.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ..utils.timeutil import utc_now

if TYPE_CHECKING:
    from .user import User




class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    owner_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prefix: Mapped[str] = mapped_column(String(8), nullable=False, unique=True, index=True)
    last4: Mapped[str] = mapped_column(String(4), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    # Reversible disable, distinct from revoke (which is permanent).
    # If both are set, revoked_at wins (semantically the token is dead).
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    # Optional expiry. NULL = never expires (the default, back-compat).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    owner: Mapped[User] = relationship("User", foreign_keys=[owner_user_id])
