"""WebAuthn / passkey credential storage.

A user can register multiple credentials (laptop Touch ID + USB key +
phone passkey, etc.). On authentication we accept any of them; the
`sign_count` is bumped to detect cloned authenticators (the spec
mandates strictly-increasing counters).

Credential IDs are opaque blobs from the authenticator. Public keys
are CBOR-encoded COSE keys per the WebAuthn spec; we store them as
bytes and let `webauthn` decode on verify.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ..utils.timeutil import utc_now

_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")

if TYPE_CHECKING:
    from .user import User




class UserWebAuthnCredential(Base):
    __tablename__ = "user_webauthn_credentials"

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Lengths match the phase8 migration. An unbounded LargeBinary is BLOB on MySQL,
    # and a UNIQUE key over a BLOB is error 1170, so building this schema from the
    # models would fail on a table alembic creates without complaint. The unique
    # constraint already indexes the column; the separate index=True described one
    # production has never had.
    credential_id: Mapped[bytes] = mapped_column(
        LargeBinary(512), nullable=False, unique=True
    )
    public_key: Mapped[bytes] = mapped_column(LargeBinary(2048), nullable=False)
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Comma-separated transports list (`usb`, `ble`, `nfc`, `internal`,
    # `hybrid`). Hint to the browser; not load-bearing for security.
    transports: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    # User-given label so they can tell credentials apart in the
    # account UI ("MacBook Touch ID", "Yubikey 5C", etc.).
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="passkey")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(), nullable=True
    )

    user: Mapped[User] = relationship("User", foreign_keys=[user_id])
