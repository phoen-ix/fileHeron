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

from datetime import datetime, timezone
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

_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")

if TYPE_CHECKING:
    from .user import User


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


class UserWebAuthnCredential(Base):
    __tablename__ = "user_webauthn_credentials"

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # `credential_id` is opaque per the spec; bytes, indexed for lookup
    # at authenticate time.
    credential_id: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False, unique=True, index=True
    )
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Comma-separated transports list (`usb`, `ble`, `nfc`, `internal`,
    # `hybrid`). Hint to the browser; not load-bearing for security.
    transports: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    # User-given label so they can tell credentials apart in the
    # account UI ("MacBook Touch ID", "Yubikey 5C", etc.).
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="passkey")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=_utcnow
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(), nullable=True
    )

    user: Mapped[User] = relationship("User", foreign_keys=[user_id])
