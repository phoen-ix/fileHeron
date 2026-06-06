"""Groups - user-defined collections of users used as share recipients.

A group whose `is_company_inbox` flag is true is treated as a landing zone
that any client connected to the org can target with an inbound share. The
typical setup is one such group called e.g. "incoming-from-clients".

Phase 4 introduces this; earlier phases stored a NULL recipient_group_id
on share_recipients, kept for forward compatibility.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ..utils.timeutil import utc_now

# Tests run on SQLite where BIGINT primary keys do NOT autoincrement;
# fall back to INTEGER so ROWID handles it.
_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")

if TYPE_CHECKING:
    from .group_member import GroupMember
    from .user import User




class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Lowercase mirror of `name`; uniqueness lives here so case-insensitive
    # collisions are rejected at insert time. Service layer is the single
    # writer (group.create / group.update both set both columns together).
    name_normalized: Mapped[str] = mapped_column(
        String(120), unique=True, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_company_inbox: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now
    )

    created_by: Mapped[User] = relationship("User", foreign_keys=[created_by_id])
    members: Mapped[list[GroupMember]] = relationship(
        "GroupMember", back_populates="group", cascade="all, delete-orphan"
    )
