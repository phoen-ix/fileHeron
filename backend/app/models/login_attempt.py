"""Login attempts log. One row per /api/auth/login call. Used by the rate
limiter for forensic queries; the actual rate-limit decision is made via
Redis sliding-window counters (services/rate_limit.py) for performance.

`email` is NULL when the input email didn't parse. `outcome` is a free
string enum so new outcomes can be added without column migration; the
canonical values are listed in the LoginOutcome enum.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base

# Same SQLite-vs-MariaDB workaround as audit_log.
_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class LoginOutcome(str, enum.Enum):
    success = "success"
    bad_password = "bad_password"
    bad_totp = "bad_totp"
    bad_recovery = "bad_recovery"
    unknown_email = "unknown_email"
    locked = "locked"
    rate_limited = "rate_limited"
    account_disabled = "account_disabled"
    email_not_verified = "email_not_verified"


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True, index=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True, index=True)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
