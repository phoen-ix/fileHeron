"""An address a share was sent to that has no account (v2.21.0).

Such a recipient is never a principal: they get the share's public link by
email, and the link - with its own password and download counter - is the only
access they have. This row records where the link was sent and whether the mail
has gone out, nothing more; no authorisation check reads it.

`notified_at` makes the announcement idempotent per address: `announce_if_ready`
already claims the share once, and the stamp keeps a later re-announce from
mailing the same address twice.

`send_link` is the SENDER's choice (v2.22.0): false records the address and
never mails it - the sender sends the link themselves. It is not governed by
the share's "notify recipients" flag, which is about account recipients.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ..utils.timeutil import utc_now
from .user import EMAIL_COLUMN_TYPE

_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")

if TYPE_CHECKING:
    from .share import Share


class ShareExternalRecipient(Base):
    __tablename__ = "share_external_recipients"
    __table_args__ = (
        Index(
            "uq_share_external_recipients_share_email",
            "share_id",
            "email",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    share_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("shares.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(EMAIL_COLUMN_TYPE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    send_link: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )

    share: Mapped[Share] = relationship("Share", back_populates="external_recipients")
