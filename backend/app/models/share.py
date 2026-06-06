"""Shares - one user wraps one or more files for one or more recipients.

Phase 3a creates a basic single-recipient model (single user_id allowed via
ShareRecipient). Phase 4 extends to multiple users + groups + the
client→company inbox group pattern.

A share has a strict expiry. The hourly ARQ cleanup job (Phase 4) walks
shares whose expires_at has passed and hard-deletes the underlying files.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ..utils.timeutil import utc_now

if TYPE_CHECKING:
    from .file import File
    from .share_recipient import ShareRecipient
    from .user import User


class ShareKind(str, enum.Enum):
    """outbound = employee/admin → client(s)/group(s).
    inbound  = client → employee(s)/inbox group."""
    outbound = "outbound"
    inbound = "inbound"


class ShareState(str, enum.Enum):
    active = "active"
    expired = "expired"
    revoked = "revoked"
    deleted = "deleted"
    # Upload never completed - the cleanup_stale_uploads cron reaps the
    # abandoned file(s) and flips the otherwise-empty share here so it leaves
    # the active sent folder. Stored as VARCHAR (native_enum=False) so adding
    # this value needs no migration; "failed" (6) fit the original length=10.
    failed = "failed"
    # Share-approval workflow (v1.24.0). When the policy requires it a new share
    # lands in `pending_approval` until an approver approves (→ active) or
    # rejects (→ rejected, bytes kept; the owner can resubmit). "pending_approval"
    # (16 chars) is why the `state` column widened to VARCHAR(20).
    pending_approval = "pending_approval"
    rejected = "rejected"




def _new_uuid() -> str:
    return str(uuid.uuid4())


class Share(Base):
    __tablename__ = "shares"
    __table_args__ = (
        # Hot path: the hourly expire_files cron + list filtering both key on
        # (state, expires_at).
        Index("ix_shares_state_expires", "state", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)

    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    kind: Mapped[ShareKind] = mapped_column(
        SAEnum(ShareKind, native_enum=False, length=10), nullable=False
    )

    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # NULL = "never expires" (v1.1.4). Cron filters using
    # `WHERE expires_at < now()` exclude NULL rows by SQL semantics, so
    # never-expire shares simply sit untouched. Public-link unlock
    # cookie code in routers/public.py special-cases None.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True, index=True)

    state: Mapped[ShareState] = mapped_column(
        SAEnum(ShareState, native_enum=False, length=20),
        nullable=False,
        default=ShareState.active,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    # Set by the share_expiring_24h_warning cron when it has fired the
    # expiring-soon notification for this share - flag is what makes the
    # job idempotent across re-runs in the same hour.
    expiring_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(), nullable=True
    )
    # Set when the share transitions to a terminal state (revoked / deleted).
    # The orphan-reclaim cron ages its grace window off this so a deliberately
    # revoked share keeps its bytes for a recovery margin before cleanup.
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    # v1.1.0 per-share download budget. NULL = unlimited (matches the
    # public_link semantic). `download_limit` is the cap chosen by the
    # sender; `downloads_remaining` decrements atomically per download
    # via the same `UPDATE … WHERE remaining > 0` pattern public_link
    # uses. Single shared budget across all recipients + sender + admins.
    download_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    downloads_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Share-approval workflow (v1.24.0). Set on the approve/reject decision;
    # `rejection_reason` carries the approver's note to the sender.
    # `notify_on_activation` freezes the sender's "notify recipients" choice at
    # create time so the deferred `share_created` dispatch (fired on approval)
    # honours it. All NULL for shares that never went through approval.
    approval_decided_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approval_decided_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notify_on_activation: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_by: Mapped[User] = relationship("User", foreign_keys=[created_by_id])
    files: Mapped[list[File]] = relationship(
        "File", back_populates="share", cascade="all, delete-orphan"
    )
    recipients: Mapped[list[ShareRecipient]] = relationship(
        "ShareRecipient", back_populates="share", cascade="all, delete-orphan"
    )
