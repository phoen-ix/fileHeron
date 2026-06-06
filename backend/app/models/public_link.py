"""Public download links - anonymous access to a share's files via a
URL-safe token, optionally protected by a password and/or limited by a
download counter.

Token: 32-byte urlsafe-base64 (43 chars, well over 256 bits of entropy).
Stored as SHA-256 hex; lookup is by indexed hash. The plaintext is
shown to the creator exactly once.

Password: optional. Argon2id-hashed because passwords have low entropy.
A failure rate-limit lives in `public_link_password_attempts` (which we
also use to email the owner on lockout).

Counter: `downloads_remaining` is NULL = unlimited; otherwise decrements
atomically on each successful file download.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ..utils.timeutil import utc_now

# BIGINT does not autoincrement under SQLite, fall through to ROWID.
_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")

if TYPE_CHECKING:
    from .share import Share
    from .user import User




def _new_uuid() -> str:
    return str(uuid.uuid4())


class PublicLink(Base):
    __tablename__ = "public_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    share_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("shares.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        unique=True,  # one public link per share - keeps the URL story simple
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    # Fernet-encrypted plaintext token, stored so the share owner can
    # re-view the URL on /share/{id} without having to revoke +
    # recreate. Lookup still goes through token_hash; this column is
    # owner-display only. Nullable for legacy rows created before this
    # column shipped (their plaintext is irrecoverable from the hash).
    token_encrypted: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    download_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    downloads_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notify_on_download: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(), nullable=True
    )

    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now
    )

    share: Mapped[Share] = relationship("Share")
    created_by: Mapped[User] = relationship("User", foreign_keys=[created_by_id])
