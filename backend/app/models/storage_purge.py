"""Stored bytes waiting to be deleted after a commit (encryption at rest).

Encrypting a stored file writes its ciphertext to a NEW locator and swaps the
row onto it; the plaintext it replaced - or, after a crash, a half-written
target - must go once nothing can need it. CLAUDE.md's rule for byte deletes
is "unlink after committing, and a deferred purge records its own failure":
by the time the swap has committed, the old locator is on no row, so no sweep
that walks `files` could ever find it again. This table is where it waits.

`reason`:
- `enc_target` - a LEASE: written before the first ciphertext byte, deleted in
  the swap's own transaction. A row still here after `not_before` means the
  encryption died, and its target is debris.
- `enc_plaintext` - the plaintext a committed swap replaced, purged once
  `not_before` passes (a grace that lets a download already streaming it end).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils.timeutil import utc_now

_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")

PURGE_ENC_TARGET = "enc_target"
PURGE_ENC_PLAINTEXT = "enc_plaintext"


class StoragePurge(Base):
    __tablename__ = "storage_purge_queue"
    __table_args__ = (Index("ix_storage_purge_queue_due", "not_before", "id"),)

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    locator: Mapped[str] = mapped_column(String(512), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    # The row the bytes belonged to (a file id, or an attachment id), for the
    # audit trail of a purge that keeps failing.
    ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    not_before: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
