"""Email-change token: stages a pending email change so ``users.email`` is
never mutated until the new address (and, in ``verify_both`` mode, the old
address too) proves control.

Single-use, 24h expiry, scoped to one user. Mirrors
``password_reset_token`` / ``email_verify_token`` but carries the *target*
address plus a per-side confirmation flag so the change applies only when all
required sides have clicked their link.

- ``new_token_hash`` — always present; the confirm link mailed to the NEW
  address.
- ``old_token_hash`` — only set in ``verify_both`` mode; the confirm link
  mailed to the OLD address. Its presence is what makes old-side confirmation
  *required*.
- ``cancel_token_hash`` — set in the pending modes; the "it wasn't me" kill
  switch mailed to the OLD address. Clicking it cancels the pending change.
- ``oidc_mode`` — the OIDC-reset policy frozen at request time so a mid-flight
  policy change can't alter an in-progress confirmation.
- ``used_at`` set ⇒ the change has been applied. ``cancelled_at`` set ⇒ the
  pending change was killed (old-email "it wasn't me", admin revoke, or
  superseded by a newer request). Both NULL ⇒ still pending.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils.timeutil import utc_now


class EmailChangeToken(Base):
    __tablename__ = "email_change_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    new_email: Mapped[str] = mapped_column(String(254), nullable=False)

    new_token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    old_token_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    cancel_token_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )

    new_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    old_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    # Frozen at request time: 'reset_setpw' | 'reset_only' | 'keep'.
    oidc_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="reset_setpw"
    )
    # NULL once the initiator's account is erased (FK SET NULL). Equal to
    # user_id ⇒ self-initiated; different ⇒ an admin staged it for the user.
    initiated_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    @property
    def requires_old(self) -> bool:
        """True when the OLD address must also confirm (verify_both mode)."""
        return self.old_token_hash is not None
