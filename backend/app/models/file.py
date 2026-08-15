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
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ..utils.timeutil import utc_now

if TYPE_CHECKING:
    from .share import Share
    from .user import User


class FileState(str, enum.Enum):
    uploading = "uploading"
    ready_unscanned = "ready_unscanned"
    clean = "clean"
    infected = "infected"
    deleted = "deleted"


class FileApprovalState(str, enum.Enum):
    """Whether this file has cleared four-eyes review.

    Four-eyes was evaluated once, in `create_share`, and never again - but the
    upload gate admits `active` as well as `pending_approval`, so an owner could
    get a benign share approved and then upload the real payload into the live
    share, reaching the recipients with no second sign-off. The share-level state
    cannot express that: flipping an active share back to `pending_approval`
    would 410 every existing recipient and darken a live public link, turning a
    routine "here's the appendix" upload into an outage.

    So the mark lives on the FILE. `approved` is the default, which is what every
    pre-existing row and every deployment with approval switched off must be.
    """
    approved = "approved"
    pending_review = "pending_review"




def _new_uuid() -> str:
    return str(uuid.uuid4())


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        # Per-user storage sum (quota display + reconcile) filters on
        # (uploaded_by_id, state).
        Index("ix_files_uploader_state", "uploaded_by_id", "state"),
    )

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

    # True when the file was served WITHOUT a trustworthy antivirus verdict,
    # because it is larger than clamd can scan (clamd clamps MaxFileSize to
    # INT_MAX ~= 2 GiB regardless of clamd.conf - see config.AV_MAX_SCAN_BYTES).
    # clamd answers "clean" for such files without reading them, so recording
    # that verdict as `clean` would be a lie. The file still reaches `clean`
    # state so it stays downloadable (deliberate product decision: fileHeron
    # supports uploads far larger than any AV can scan), but this flag is what
    # the API, the UI warning and the audit trail read (audit 2026-07-30).
    av_unscanned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0", index=True
    )

    # Files added to a share that was ALREADY approved wait for their own
    # decision; everything else is `approved` on arrival. Indexed because the
    # approvals queue filters on it.
    approval_state: Mapped[FileApprovalState] = mapped_column(
        SAEnum(FileApprovalState, native_enum=False, length=20),
        nullable=False,
        default=FileApprovalState.approved,
        server_default=FileApprovalState.approved.value,
        index=True,
    )

    uploaded_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    # Last time tusd reported bytes arriving for this row (post-receive hook).
    # `created_at` is stamped at /api/uploads/init, before the first byte, so it
    # says when the upload STARTED, never whether it is still going - reaping on
    # it killed every transfer slower than the stale cutoff. NULL means "no
    # progress reported yet"; every reader COALESCEs to created_at, which keeps
    # pre-upgrade rows and direct uploads behaving exactly as before.
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    # Set during the upload, cleared after post-finish move.
    tus_upload_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    share: Mapped[Share] = relationship("Share", back_populates="files")
    uploaded_by: Mapped[User] = relationship("User", foreign_keys=[uploaded_by_id])
