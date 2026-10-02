"""Secret requests (v2.24.0): asking someone for a password or other short secret.

The requester names who may answer - people with an account, groups, email
addresses without an account (each gets its own link, mailed to it) and/or a
copyable link - says what they want (`label`, an optional `note`), how long the
request stays open, and how the answer may be read. The FIRST answer fulfils
the request: it becomes an ordinary secret (models/secret.py, `is_answer`) whose
only reader is the requester, and every other way in closes.

The requester may set a passphrase. Only its PUBLIC half is kept here
(`kdf_salt`, `kdf_params`, `public_key`): the answer is sealed to it, and
nothing on the server can open it until the requester types the passphrase
again (utils/crypto.py).

Who may answer is `secret_request_targets` plus, for a group, the membership
snapshot in `secret_request_group_members`: a member must have been one when
the request was made AND still be one - the rule secrets use.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ..utils.timeutil import utc_now
from .secret import SecretRecipientKind
from .user import EMAIL_COLUMN_TYPE, User


class SecretRequestState(str, enum.Enum):
    open = "open"
    # Answered; `Secret.request_id` points back at it.
    fulfilled = "fulfilled"
    # Withdrawn by the requester or an admin, or by a config import.
    cancelled = "cancelled"
    # Nobody answered in time.
    expired = "expired"


def _new_uuid() -> str:
    return str(uuid.uuid4())


class SecretRequest(Base):
    __tablename__ = "secret_requests"
    __table_args__ = (
        # The expiry sweep keys on (state, expires_at).
        Index("ix_secret_requests_state_expires", "state", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    requester_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # What is asked for, and an optional note. Both are shown to whoever may
    # answer, link holders included - never a secret themselves.
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    state: Mapped[SecretRequestState] = mapped_column(
        SAEnum(SecretRequestState, native_enum=False, length=10),
        nullable=False,
        default=SecretRequestState.open,
        index=True,
    )
    # Open until; the answer path checks it itself, never leaving it to the sweep.
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)

    # How the answer may be read, chosen by the requester: a view limit and/or
    # a lifetime counted from the moment it arrives. At least one is set.
    answer_max_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answer_expires_in_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # The requester's passphrase layer: public half only (all NULL without one).
    kdf_salt: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kdf_params: Mapped[str | None] = mapped_column(String(64), nullable=True)
    public_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    fulfilled_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # The target row the answer came through (no FK: the targets are this
    # row's children, and a cycle buys nothing).
    fulfilled_via_target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)

    requester: Mapped[User] = relationship("User", foreign_keys=[requester_id])
    targets: Mapped[list[SecretRequestTarget]] = relationship(
        "SecretRequestTarget",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="SecretRequestTarget.id",
    )


class SecretRequestTarget(Base):
    __tablename__ = "secret_request_targets"
    __table_args__ = (
        Index("uq_secret_request_targets_user", "request_id", "target_user_id", unique=True),
        Index("uq_secret_request_targets_group", "request_id", "target_group_id", unique=True),
        Index("uq_secret_request_targets_email", "request_id", "email", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("secret_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[SecretRecipientKind] = mapped_column(
        SAEnum(SecretRecipientKind, native_enum=False, length=8), nullable=False
    )
    target_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # BigInteger: `groups.id` is BIGINT on MariaDB (see secret_recipients).
    target_group_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Exactly as typed, never looked up against users (the secrets rule).
    email: Mapped[str | None] = mapped_column(EMAIL_COLUMN_TYPE, nullable=True)
    # email + link only. Lookup by hash; the Fernet copy lets the requester copy
    # the link again. NULLed once the request is no longer open.
    token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    token_encrypted: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)

    request: Mapped[SecretRequest] = relationship("SecretRequest", back_populates="targets")


class SecretRequestGroupMember(Base):
    """Who was in a target group when the request was made."""

    __tablename__ = "secret_request_group_members"

    target_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("secret_request_targets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
