"""Append-only outbound email log (v1.11.0).

One row per email across every send path - queued notifications (ARQ
`send_email_job`), the synchronous auth-flow direct senders, the admin
test-send, and the dev logs-fallback. The row is created with
`status=queued` and UPDATEd in place to its terminal status, so worker
retries don't multiply rows (`attempts` climbs instead).

Bodies are stored with one-time auth-link tokens **masked at rest** (see
`services/mail_log.py`), so the log can never be used to take over an
account and a short-lived token doesn't outlive its TTL in a browsable
log. `masked` gates the admin resend action. Body columns are
`deferred=True` so the high-volume list + CSV queries never load them -
only the single-row detail endpoint pulls a body.
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
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils.timeutil import utc_now

# BigInteger PK doesn't autoincrement on SQLite; INTEGER there (tests).
_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")
# HTML emails routinely exceed MySQL TEXT's 64 KB ceiling.
_Body = Text().with_variant(LONGTEXT(), "mysql")


class EmailStatus(str, enum.Enum):
    queued = "queued"
    sent = "sent"
    failed = "failed"  # SMTP rejected (5xx, incl. a refused recipient) or a direct-sender exception
    # Unexpected non-SMTP error. A hard bounce used to land HERE, because
    # SMTPRecipientsRefused is not an SMTPResponseException - so the commonest
    # delivery failure was recorded as, and triaged as, a code bug.
    error = "error"


class EmailVia(str, enum.Enum):
    queued = "queued"              # ARQ send_email_job (notifications)
    direct = "direct"              # services/email.py synchronous auth senders
    test = "test"                  # admin SMTP test-send
    dev_fallback = "dev_fallback"  # SMTP unconfigured → stdout, not actually sent
    resend = "resend"              # admin re-enqueued a prior logged email


class EmailLog(Base):
    __tablename__ = "email_log"
    __table_args__ = (
        Index("ix_email_log_recipient_created", "recipient_user_id", "created_at"),
        Index("ix_email_log_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, index=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(), nullable=True, default=utc_now, onupdate=utc_now
    )

    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    recipient_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    category: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    template_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    via: Mapped[EmailVia] = mapped_column(
        SAEnum(EmailVia, native_enum=False, length=16),
        nullable=False,
        default=EmailVia.queued,
    )
    status: Mapped[EmailStatus] = mapped_column(
        SAEnum(EmailStatus, native_enum=False, length=12),
        nullable=False,
        default=EmailStatus.queued,
        index=True,
    )

    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    # Deferred: never loaded by the list/CSV queries, only the detail endpoint.
    body_text: Mapped[str | None] = mapped_column(_Body, nullable=True, deferred=True)
    body_html: Mapped[str | None] = mapped_column(_Body, nullable=True, deferred=True)
    masked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    smtp_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Set on a resend row → points back at the email it was re-sent from.
    source_log_id: Mapped[int | None] = mapped_column(
        _BigIntPK, ForeignKey("email_log.id", ondelete="SET NULL"), nullable=True
    )
