"""Upload endpoints - TUS-init + small-file direct multipart."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from sqlalchemy.orm import Session

from ..config import settings
from ..dependencies import get_db, require_scope
from ..middleware.errors import AppError
from ..models.share import ShareState
from ..models.user import User
from ..schemas.upload import DirectUploadResponse, UploadInitRequest, UploadInitResponse
from ..services import file as file_svc
from ..services import quota as quota_svc
from ..services import share as share_svc
from ..services import tus_signing as ts_svc
from ..services.storage_backend import get_storage_backend
from ..utils.timeutil import utc_now

router = APIRouter(prefix="/api/uploads", tags=["uploads"])
logger = logging.getLogger("fileheron.uploads")


def _refuse_if_storage_critical(db: Session) -> None:
    """Block NEW uploads when the disk_check cron has flagged the storage
    volume critically low. Downloads are deliberately unaffected. The flag is
    the fast path (no statvfs on the hot path); the cron keeps it current."""
    from ..services import settings as settings_svc
    if settings_svc.get_bool(db, settings_svc.Keys.STORAGE_CRITICAL_LOW, default=False):
        raise AppError(
            507,
            "STORAGE_CRITICAL_LOW",
            "Server storage is critically low. Uploads are temporarily unavailable.",
        )


@router.post("/init", response_model=UploadInitResponse)
def init_upload(
    payload: UploadInitRequest,
    user: User = Depends(require_scope("files:upload")),
    db: Session = Depends(get_db),
) -> UploadInitResponse:
    """Authorise an upload. Returns a signed envelope the client embeds in
    Upload-Metadata. tusd validates on every hook by re-HMAC-ing."""
    share = share_svc.get_share_or_404(db, payload.share_id)
    if share.state not in (ShareState.active, ShareState.pending_approval):
        # The owner may keep assembling a share that's awaiting approval.
        raise AppError(409, "SHARE_NOT_ACTIVE", "Share is not active.")
    if share.created_by_id != user.id:
        raise AppError(403, "FORBIDDEN", "Only the share owner can upload to it.")

    from ..services import maintenance as maintenance_svc
    maintenance_svc.refuse_if_maintenance(db, kind="upload")
    _refuse_if_storage_critical(db)

    # Pre-flight quota check (best-effort; pre-create hook re-checks).
    quota_limit = user.quota_bytes if user.quota_bytes is not None else 0
    if quota_limit > 0 and quota_svc.used_bytes(user_id=user.id) + payload.size_bytes > quota_limit:
        raise AppError(
            413,
            "QUOTA_EXCEEDED",
            "This upload would exceed your storage quota.",
            details={"quota_bytes": user.quota_bytes},
        )

    file_row = file_svc.create_pending(
        db,
        share=share,
        uploader=user,
        original_filename=payload.filename,
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
    )
    db.commit()

    envelope_exp = int(time.time()) + 3600  # 1h
    envelope = {
        "v": 1,
        "share_id": share.id,
        "file_id": file_row.id,
        "owner_user_id": user.id,
        "filename": payload.filename,
        "mime_type": payload.mime_type,
        "max_size": payload.size_bytes,
        "exp": envelope_exp,
    }
    payload_b64, sig = ts_svc.sign_envelope(envelope)
    metadata_header = ts_svc.build_upload_metadata_header(
        payload_b64=payload_b64, sig_hex=sig, filename=payload.filename
    )

    return UploadInitResponse(
        file_id=file_row.id,
        tus_endpoint=settings.TUS_PUBLIC_BASE,
        upload_metadata_header=metadata_header,
        expires_at=datetime.fromtimestamp(envelope_exp, tz=timezone.utc),
    )


@router.post(
    "/direct", response_model=DirectUploadResponse, status_code=status.HTTP_201_CREATED
)
async def direct_upload(
    request: Request,
    share_id: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(require_scope("files:upload")),
    db: Session = Depends(get_db),
) -> DirectUploadResponse:
    """Multipart upload for files <= MAX_DIRECT_UPLOAD_BYTES. Single
    round-trip; for scripts that don't want a TUS dependency.
    Files larger than the cap MUST go through /api/uploads/init + TUS."""
    share = share_svc.get_share_or_404(db, share_id)
    if share.state not in (ShareState.active, ShareState.pending_approval):
        # The owner may keep assembling a share that's awaiting approval.
        raise AppError(409, "SHARE_NOT_ACTIVE", "Share is not active.")
    if share.created_by_id != user.id:
        raise AppError(403, "FORBIDDEN", "Only the share owner can upload to it.")

    from ..services import maintenance as maintenance_svc
    maintenance_svc.refuse_if_maintenance(db, kind="upload")
    _refuse_if_storage_critical(db)

    # Stream-check size as we read.
    import hashlib
    import os
    import tempfile

    from ..services import settings_registry
    cap = int(settings_registry.effective(db, settings_registry.K.MAX_DIRECT_UPLOAD_BYTES))
    received = 0
    reserved = 0
    sha = hashlib.sha256()
    when = utc_now()
    backend = get_storage_backend()

    Path(settings.TUS_UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=settings.TUS_UPLOAD_DIR, suffix=".part")
    finalized = False
    locator = None
    file_row = None
    try:
        # Stream each chunk straight to the temp file so resident memory stays
        # bounded by the 1 MiB chunk size, NOT the (admin-tunable) size cap - a
        # list[bytes] buffer let a few concurrent direct uploads OOM-kill the
        # backend (audit M3/L15). Staged in TUS_UPLOAD_DIR so the local backend's
        # finalize is a same-filesystem rename.
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)  # 1 MiB
                if not chunk:
                    break
                received += len(chunk)
                if received > cap:
                    raise AppError(
                        413,
                        "DIRECT_UPLOAD_TOO_LARGE",
                        f"Direct upload limit is {cap} bytes; use TUS for larger files.",
                    )
                sha.update(chunk)
                out.write(chunk)

        quota_svc.reserve_bytes(db, user=user, additional_bytes=received)
        reserved = received

        file_row = file_svc.create_pending(
            db,
            share=share,
            uploader=user,
            original_filename=file.filename or "upload.bin",
            mime_type=file.content_type or "application/octet-stream",
            size_bytes=received,
        )
        db.flush()

        locator = backend.generate_locator(file_row.id, when)
        backend.finalize(tmp_path, locator)
        finalized = True
    except Exception:
        # Release any reservation so a finalize/persist failure can't leak quota
        # against the user (audit L5); the DB row is rolled back by get_db.
        if reserved:
            quota_svc.release_bytes(user_id=user.id, bytes_to_free=reserved)
        raise
    finally:
        if not finalized and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    file_row.storage_path = locator
    file_row.state = file_row.state.__class__.ready_unscanned
    file_row.sha256_hex = sha.hexdigest()
    file_row.finalized_at = when
    db.commit()

    # Enqueue AV scan (Phase 5). Same path as the tusd post-finish hook.
    # `await` the async variant: this route is `async def`, and the
    # sync `enqueue` would otherwise have to detour around the running
    # loop. (See services/job_queue.py.)
    from ..services import job_queue
    try:
        await job_queue.aenqueue("av_scan_file", file_row.id)
    except Exception:
        # The file is already committed; a Redis blip must not 500 a successful
        # upload. cleanup_stale_uploads re-enqueues scans for rows left in
        # ready_unscanned (mirrors the tus post-finish hook's swallow-and-log).
        logger.warning(
            "av scan enqueue failed for %s; stale-upload cron will recover", file_row.id
        )

    return DirectUploadResponse(
        file_id=file_row.id, size_bytes=received, sha256_hex=file_row.sha256_hex
    )
