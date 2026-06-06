"""Append-only download log.

Phase 3a logs every successful auth-gated download. Phase 5 adds public-link
downloads (with `accessed_by_user_id=NULL` and `via=public`).

`ua_fingerprint_hash` reuses utils/ua_fingerprint:ua_fingerprint_hash to
strip patch versions - same value Phase 7 uses for the new-device alert.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils.timeutil import utc_now

# In SQLite, BigInteger PK does NOT autoincrement.
_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class DownloadVia(str, enum.Enum):
    auth = "auth"            # session/JWT - recipient on the share
    api_token = "api_token"  # programmatic
    public = "public"        # /d/{token} - Phase 5




class DownloadLog(Base):
    __tablename__ = "download_log"

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)

    file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    share_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("shares.id", ondelete="CASCADE"), nullable=False, index=True
    )

    accessed_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, index=True
    )

    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    ua_fingerprint_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    bytes_served: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    via: Mapped[DownloadVia] = mapped_column(
        SAEnum(DownloadVia, native_enum=False, length=12), nullable=False, default=DownloadVia.auth
    )
