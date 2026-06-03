"""Forensic log of every public-link password unlock attempt.

Used by the rate limiter (last N within a window) and to email the owner
on lockout. Append-only.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base

_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")

if TYPE_CHECKING:
    from .public_link import PublicLink


class PublicLinkAttemptOutcome(str, enum.Enum):
    success = "success"
    failure = "failure"
    locked = "locked"


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


class PublicLinkAttempt(Base):
    __tablename__ = "public_link_password_attempts"

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    public_link_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("public_links.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    outcome: Mapped[PublicLinkAttemptOutcome] = mapped_column(
        SAEnum(PublicLinkAttemptOutcome, native_enum=False, length=20),
        nullable=False,
    )
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=_utcnow, index=True
    )

    public_link: Mapped[PublicLink] = relationship("PublicLink")
