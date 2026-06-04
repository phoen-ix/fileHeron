"""Per-user known device fingerprints. Phase 1b records rows on each
successful login; Phase 7 fires "login from new device" emails when a
(ua_fingerprint_hash, ip_geohash) pair is missing.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils.timeutil import utc_now

_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")




class KnownDevice(Base):
    __tablename__ = "known_devices"

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ua_fingerprint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_geohash: Mapped[str] = mapped_column(String(8), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    last_seen: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        UniqueConstraint("user_id", "ua_fingerprint_hash", "ip_geohash", name="uq_known_device"),
    )
