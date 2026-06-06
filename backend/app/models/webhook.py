"""Outbound webhooks - admin-registered endpoints that receive a signed HTTP
POST on chosen events (v1.19.0).

`webhooks` is the (low-volume) subscription registry; `webhook_deliveries` is the
(high-volume) per-attempt log that backs the admin "Deliveries" view + retry.
The signing secret is Fernet-encrypted at rest (same helpers as the OIDC client
secret) and only ever shown once, on create.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils.timeutil import utc_now

# BIGINT doesn't autoincrement under SQLite; fall through to INTEGER (ROWID).
_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class WebhookDeliveryStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    failed = "failed"


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # Fernet-encrypted HMAC signing secret (utils.crypto.encrypt_setting).
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # JSON list of subscribed event names (services/webhook.WEBHOOK_EVENTS, or "*").
    event_types: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    webhook_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # The signed payload - kept so a failed delivery can be retried verbatim.
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        SAEnum(WebhookDeliveryStatus, native_enum=False, length=12),
        nullable=False,
        default=WebhookDeliveryStatus.pending,
    )
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, index=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
