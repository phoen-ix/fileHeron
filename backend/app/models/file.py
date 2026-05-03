"""Stored files. One row per finalized (or in-flight) upload.

- `id` is a UUID assigned by the backend during /api/uploads/init. It's the
  on-disk filename (under STORAGE_ROOT/yyyy/mm/{id}.bin) and also rides
  through tusd inside the signed Upload-Metadata envelope.
- `original_filename` is the human filename, stored separately so the disk
  layout never has to encode user-provided strings.
- `state` walks through: uploading → ready_unscanned → (clean | infected) →
  deleted. Phase 3a only ever reaches ready_unscanned (no AV yet); Phase 5
  adds the AV worker that flips to clean / infected.
- `tus_upload_id` is the working-dir filename tusd assigned the upload (used
  during the in-flight period); cleared after post-finish move.
- `sha256_hex` is computed in Phase 3a/3b best-effort and verified in P5.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Enum as SAEnum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base

if TYPE_CHECKING:
    from .share import Share
    from .user import User


class FileState(str, enum.Enum):
    uploading = "uploading"
    ready_unscanned = "ready_unscanned"
    clean = "clean"
    infected = "infected"
    deleted = "deleted"


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class File(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    share_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("shares.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sha256_hex: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    state: Mapped[FileState] = mapped_column(
        SAEnum(FileState, native_enum=False, length=20),
        nullable=False,
        default=FileState.uploading,
        index=True,
    )

    uploaded_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set during the upload, cleared after post-finish move.
    tus_upload_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    share: Mapped["Share"] = relationship("Share", back_populates="files")
    uploaded_by: Mapped["User"] = relationship("User", foreign_keys=[uploaded_by_id])
