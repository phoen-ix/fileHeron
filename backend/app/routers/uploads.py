"""Upload endpoints — TUS-init + small-file direct multipart."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from sqlalchemy.orm import Session

from ..config import settings
from ..dependencies import get_actor, get_db
from ..middleware.errors import AppError
from ..models.share import ShareState
from ..models.user import User
from ..schemas.upload import DirectUploadResponse, UploadInitRequest, UploadInitResponse
from ..services import file as file_svc
from ..services import quota as quota_svc
from ..services import share as share_svc
from ..services import tus_signing as ts_svc

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def _utcnow_aware() -> datetime:
    return datetime.now(tz=timezone.utc)


@router.post("/init", response_model=UploadInitResponse)
def init_upload(
    payload: UploadInitRequest,
    user: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> UploadInitResponse:
    """Authorise an upload. Returns a signed envelope the client embeds in
    Upload-Metadata. tusd validates on every hook by re-HMAC-ing."""
    share = share_svc.get_share_or_404(db, payload.share_id)
    if share.state != ShareState.active:
        raise AppError(409, "SHARE_NOT_ACTIVE", "Share is not active.")
    if share.created_by_id != user.id:
        raise AppError(403, "FORBIDDEN", "Only the share owner can upload to it.")

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
    user: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> DirectUploadResponse:
    """Multipart upload for files <= MAX_DIRECT_UPLOAD_BYTES. Single
    round-trip; for scripts that don't want a TUS dependency.
    Files larger than the cap MUST go through /api/uploads/init + TUS."""
    share = share_svc.get_share_or_404(db, share_id)
    if share.state != ShareState.active:
        raise AppError(409, "SHARE_NOT_ACTIVE", "Share is not active.")
    if share.created_by_id != user.id:
        raise AppError(403, "FORBIDDEN", "Only the share owner can upload to it.")

    # Stream-check size as we read.
    cap = int(settings.MAX_DIRECT_UPLOAD_BYTES)
    chunks: list[bytes] = []
    received = 0
    import hashlib
    sha = hashlib.sha256()

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
        chunks.append(chunk)

    # Quota
    quota_svc.reserve_bytes(db, user=user, additional_bytes=received)

    # Persist
    file_row = file_svc.create_pending(
        db,
        share=share,
        uploader=user,
        original_filename=file.filename or "upload.bin",
        mime_type=file.content_type or "application/octet-stream",
        size_bytes=received,
    )
    db.flush()

    # Write to permanent storage (no tusd involvement).
    when = _utcnow_aware().replace(tzinfo=None)
    dest = file_svc.storage_path_for(file_row.id, when)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        for chunk in chunks:
            out.write(chunk)

    file_row.storage_path = str(dest)
    file_row.state = file_row.state.__class__.ready_unscanned
    file_row.sha256_hex = sha.hexdigest()
    file_row.finalized_at = when
    db.commit()

    # Enqueue AV scan (Phase 5). Same path as the tusd post-finish hook.
    # `await` the async variant: this route is `async def`, and the
    # sync `enqueue` would otherwise have to detour around the running
    # loop. (See services/job_queue.py.)
    from ..services import job_queue
    await job_queue.aenqueue("av_scan_file", file_row.id)

    return DirectUploadResponse(
        file_id=file_row.id, size_bytes=received, sha256_hex=file_row.sha256_hex
    )


# Suppress an unused-import for the file_id timedelta import — the linter
# would otherwise complain since timedelta isn't used yet (it will be in P5
# for public-link expiry math). Keep the import for forward consistency.
_ = timedelta
_ = Path
