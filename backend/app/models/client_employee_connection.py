"""Client ↔ employee connections.

A client and an employee are "connected" if either:
- the employee invited the client (source=invite, sticky), or
- both belong to at least one common group (source=shared_group, dynamic —
  removed when the last shared group is left).

Connections are the basis for the recipient-search ACL: clients can only
target connected employees + company-inbox groups; employees see all
clients in /api/users/search.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base

if TYPE_CHECKING:
    from .user import User


class ConnectionSource(str, enum.Enum):
    invite = "invite"
    shared_group = "shared_group"


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


class ClientEmployeeConnection(Base):
    __tablename__ = "client_employee_connections"

    client_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    employee_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[ConnectionSource] = mapped_column(
        SAEnum(ConnectionSource, native_enum=False, length=20),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=_utcnow
    )

    client: Mapped["User"] = relationship("User", foreign_keys=[client_user_id])
    employee: Mapped["User"] = relationship("User", foreign_keys=[employee_user_id])
