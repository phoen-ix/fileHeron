"""Inbound mailbox - messages fetched from the configured account over IMAP
(v1.27.0).

One row per ingested message. Dedup is by ``(uidvalidity, imap_uid)`` (the IMAP
server's stable identity) plus ``message_id`` as a backstop, so re-polling never
double-ingests - even in the "leave untouched" post-fetch mode. Bodies are
``deferred`` so the list + count queries never load them; only the detail
endpoint pulls them. Inbound HTML is sanitised (nh3) at ingest time.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ..utils.timeutil import utc_now

_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")
_Body = Text().with_variant(LONGTEXT(), "mysql")


class MessageClass(str, enum.Enum):
    normal = "normal"          # a genuine reply / human message
    bounce = "bounce"          # delivery-status notification (DSN)
    auto_reply = "auto_reply"  # vacation / out-of-office / auto-ack


class MessageStatus(str, enum.Enum):
    new = "new"
    read = "read"
    archived = "archived"


class InboundMessage(Base):
    __tablename__ = "inbound_messages"
    __table_args__ = (
        UniqueConstraint("uidvalidity", "imap_uid", name="uq_inbound_uid"),
        Index("ix_inbound_class_created", "classification", "created_at"),
        Index("ix_inbound_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, index=True
    )
    # Date header of the message itself (may differ from when we fetched it).
    received_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    sender_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    to_addr: Mapped[str | None] = mapped_column(String(320), nullable=True)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)

    message_id: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(320), nullable=True)
    imap_uid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uidvalidity: Mapped[int] = mapped_column(BigInteger, nullable=False)

    classification: Mapped[MessageClass] = mapped_column(
        SAEnum(MessageClass, native_enum=False, length=12),
        nullable=False, default=MessageClass.normal, index=True,
    )
    status: Mapped[MessageStatus] = mapped_column(
        SAEnum(MessageStatus, native_enum=False, length=12),
        nullable=False, default=MessageStatus.new, index=True,
    )

    body_text: Mapped[str | None] = mapped_column(_Body, nullable=True, deferred=True)
    # Sanitised (nh3) at ingest - safe to render in a sandboxed iframe.
    body_html: Mapped[str | None] = mapped_column(_Body, nullable=True, deferred=True)
    has_attachments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    attachments: Mapped[list[InboundAttachment]] = relationship(  # noqa: F821
        "InboundAttachment", back_populates="message", cascade="all, delete-orphan"
    )
