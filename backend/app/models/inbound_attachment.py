"""Attachments on inbound messages (v1.27.0).

Bytes are stored via the pluggable storage backend (``services/storage_backend``)
under ``storage_key`` and ClamAV-scanned inline at ingest
(``services/inbound_mail.py``); anything left ``pending`` (e.g. a ClamAV outage)
is settled later by the ``rescan_inbound_attachments`` cron. ``av_state`` gates
the download endpoint.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .inbound_message import InboundMessage

import enum

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base

_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class AttachmentAVState(str, enum.Enum):
    pending = "pending"
    clean = "clean"
    infected = "infected"


class InboundAttachment(Base):
    __tablename__ = "inbound_attachments"

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        _BigIntPK, ForeignKey("inbound_messages.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(127), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    av_state: Mapped[AttachmentAVState] = mapped_column(
        SAEnum(AttachmentAVState, native_enum=False, length=10),
        nullable=False, default=AttachmentAVState.pending,
    )

    message: Mapped[InboundMessage] = relationship(
        "InboundMessage", back_populates="attachments"
    )
