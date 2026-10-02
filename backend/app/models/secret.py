"""Secrets (v2.24.0): a password or other short text handed to recipients, readable
a limited number of times and/or until a date, then destroyed.

The content is never stored readable. `ciphertext` is the text under a key made
for this one secret; `key_encrypted` is that key wrapped by the instance key
(the Fernet key derived from JWT_SECRET, the same one TOTP secrets use) and, when
the sender set a passphrase, by an Argon2id key derived from it underneath. The
passphrase itself is stored nowhere. When a secret ends - its views are used
up, it expires, or someone burns it - `ciphertext`, `key_encrypted` and the KDF
fields are set to NULL in the same transaction as the state change, so an ended
secret cannot be recovered from the database at all.

Who may read it is `secret_recipients` (a user, a group, an email address with
no account, or the copyable link) plus, for a group, the membership snapshot in
`secret_group_members`: a member must have been one when the secret was sent AND
still be one now. `secret_user_states` holds one row per account person who can
read it (direct recipients and snapshot members), created at send time so every
counter is a conditional UPDATE rather than an insert race.

How the view limit counts is `view_scope` - see services/secret_reveal.py.

An ANSWER to a secret request (models/secret_request.py) is an ordinary secret
whose only reader is the requester. It carries what it needs to stand alone once
the request is pruned: `is_answer`, the request's label as its own, and - when
nobody signed in wrote it - `created_by_id` NULL plus `answered_by_email` for an
answer through a mailed link (NULL too for the copyable link). The requester's
optional passphrase layer is `req_*` (utils/crypto.py).
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ..utils.timeutil import utc_now
from .user import EMAIL_COLUMN_TYPE

_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")

if TYPE_CHECKING:
    from .user import User


class SecretState(str, enum.Enum):
    active = "active"
    # Every view was used (or nobody who could still read it is left).
    burned = "burned"
    expired = "expired"
    # Ended early: the sender or an admin burned it, or a config import did.
    revoked = "revoked"


class SecretViewScope(str, enum.Enum):
    # Every person (each user, each eligible group member, each address, the
    # link) gets `max_views` of their own.
    per_person = "per_person"
    # Every recipient ENTRY gets `max_views`; a group's members share its pool.
    per_recipient = "per_recipient"
    # `max_views` in total, across everyone.
    total = "total"


class SecretRecipientKind(str, enum.Enum):
    user = "user"
    group = "group"
    email = "email"
    link = "link"


class SecretAccessOutcome(str, enum.Enum):
    viewed = "viewed"
    wrong_passphrase = "wrong_passphrase"
    locked = "locked"
    burned = "burned"


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Secret(Base):
    __tablename__ = "secrets"
    __table_args__ = (
        # The expiry sweep keys on (state, expires_at).
        Index("ix_secrets_state_expires", "state", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    # NULL only for an answer to a request written by someone without an
    # account (through a mailed link or the request link).
    created_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # The only part of a secret shown before it is revealed, and the only part
    # that appears in notices and mail. Chosen by the sender as such.
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # NULL once the secret has ended (shredded). Text: 10,000 characters of
    # 4-byte UTF-8 is a ~53 KB Fernet token, under TEXT's 64 KB.
    ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Present iff the sender set a passphrase. Hex salt; "t=..,m=..,p=.." params,
    # stored per secret so a later change to the ARGON2_* settings cannot make
    # an existing secret unreadable.
    kdf_salt: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kdf_params: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Survives the shred, so an ended secret still says it had one.
    has_passphrase: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    # NULL = no view limit (the secret then has an expiry).
    max_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    view_scope: Mapped[SecretViewScope] = mapped_column(
        SAEnum(SecretViewScope, native_enum=False, length=16),
        nullable=False,
        default=SecretViewScope.per_person,
    )
    # Every view, whatever the scope; the budget itself only in `total` scope.
    views_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # NULL = no expiry (the secret then has a view limit).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    notify_on_view: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # Wrong passphrases a principal may enter before the secret burns for them.
    # NULL = throttle-and-lock instead. Frozen at creation: a stored fact, not a
    # live reading of the admin setting.
    burn_after_failures: Mapped[int | None] = mapped_column(Integer, nullable=True)

    state: Mapped[SecretState] = mapped_column(
        SAEnum(SecretState, native_enum=False, length=10),
        nullable=False,
        default=SecretState.active,
        index=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)

    # --- answers to a secret request ---
    request_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("secret_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Survives the request's pruning: the requester may burn their answer.
    is_answer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # The address a mailed request link went to, when that link answered.
    answered_by_email: Mapped[str | None] = mapped_column(EMAIL_COLUMN_TYPE, nullable=True)
    # The requester's passphrase layer (all NULL without one); shredded with
    # the rest when the secret ends.
    req_kdf_salt: Mapped[str | None] = mapped_column(String(64), nullable=True)
    req_kdf_params: Mapped[str | None] = mapped_column(String(64), nullable=True)
    req_ephemeral_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Survives the shred, like has_passphrase.
    has_request_passphrase: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    created_by: Mapped[User | None] = relationship("User", foreign_keys=[created_by_id])
    recipients: Mapped[list[SecretRecipient]] = relationship(
        "SecretRecipient",
        back_populates="secret",
        cascade="all, delete-orphan",
        order_by="SecretRecipient.id",
    )


class SecretRecipient(Base):
    __tablename__ = "secret_recipients"
    __table_args__ = (
        Index("uq_secret_recipients_user", "secret_id", "recipient_user_id", unique=True),
        Index("uq_secret_recipients_group", "secret_id", "recipient_group_id", unique=True),
        Index("uq_secret_recipients_email", "secret_id", "email", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    secret_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("secrets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[SecretRecipientKind] = mapped_column(
        SAEnum(SecretRecipientKind, native_enum=False, length=8), nullable=False
    )
    recipient_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # BigInteger: `groups.id` is BIGINT on MariaDB, and InnoDB refuses a
    # foreign key between columns of different integer types.
    recipient_group_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Exactly as typed (normalised), never looked up against users: an answer of
    # "this address has an account" would tell the sender that a client they
    # are not connected to exists. Same rule as share_external_recipients.
    email: Mapped[str | None] = mapped_column(EMAIL_COLUMN_TYPE, nullable=True)

    # email + link only. Lookup by hash; the Fernet copy lets the sender copy
    # the link again. NULLed when the secret ends.
    token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    token_encrypted: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # The pool for `per_recipient` scope (a group's members share it) and the
    # per-principal counter for an address or the link.
    views_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Wrong passphrases entered through this address/link.
    failed_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    # Burned for this address/link after too many wrong passphrases.
    burned_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    # A link the sender replaced or removed.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    # When the link was mailed to an address.
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)

    secret: Mapped[Secret] = relationship("Secret", back_populates="recipients")


class SecretGroupMember(Base):
    """Who was in a recipient group when the secret was sent."""

    __tablename__ = "secret_group_members"

    recipient_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("secret_recipients.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class SecretUserState(Base):
    """One account person who can read the secret: their own view counter (the
    budget in `per_person` scope, a tally otherwise), their wrong passphrases,
    and whether the secret has burned for them."""

    __tablename__ = "secret_user_states"

    secret_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("secrets.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    views_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failed_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    burned_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class SecretAccessEvent(Base):
    """Every view, wrong passphrase, lock and burn. The sender's view log, and
    the window the passphrase throttle counts in."""

    __tablename__ = "secret_access_events"

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    secret_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("secrets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The address/link it came through, or the user/group entry an account
    # person reached it by.
    recipient_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("secret_recipients.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    outcome: Mapped[SecretAccessOutcome] = mapped_column(
        SAEnum(SecretAccessOutcome, native_enum=False, length=20), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, index=True
    )
