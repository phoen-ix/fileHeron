"""Refresh token storage. Tokens are 64 raw bytes of crypto-random data,
hashed with SHA-256 (high entropy → no Argon2 needed). Rotation:

- on `/api/auth/refresh`, the current token is marked revoked, a new one is
  issued, and `replaced_by_id` links the new to the old.
- a revoked token is NOT automatically theft, and the discriminator is
  `replaced_by_id`. NULL means the row was revoked deliberately (logout-others,
  session-cap eviction, password change/reset, email change, admin revoke,
  config restore) and the refresh fails softly with INVALID_REFRESH. Set means a
  ROTATED link is being replayed - that is the theft signal, and it revokes
  every one of the user's refresh tokens (not merely the chain descendants, as
  this said) and audit-logs `refresh_token_reused`.
- clients must not refresh concurrently on one cookie: two racers are either
  soft-failed or, if the loser reads after the winner commits, taken for that
  replay. Both SPA and desktop client serialise their refreshes for this reason.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ..utils.timeutil import utc_now

if TYPE_CHECKING:
    from .user import User




class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        # Admin session list + active-session lookups filter on
        # (user_id, revoked_at, expires_at).
        Index("ix_refresh_tokens_user_active", "user_id", "revoked_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    # Last activity on this session. Because the token rotates on every refresh
    # (a new head row is minted), `created_at` is threaded forward to mean the
    # original sign-in time while `last_used_at` advances to the latest rotation.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    # Self-FK for rotation chain. NULL until this token is rotated (then points
    # at the successor). When reuse-detection fires, we walk this chain to
    # revoke the whole family.
    replaced_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )

    created_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)  # IPv6 textual length
    created_ua: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped[User] = relationship("User", back_populates="refresh_tokens", foreign_keys=[user_id])
