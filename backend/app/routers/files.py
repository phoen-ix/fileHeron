"""/api/files/* — download a file (auth-gated) and delete a file."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..dependencies import get_actor, get_db
from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.download_log import DownloadLog, DownloadVia
from ..models.file import File, FileState
from ..models.share import Share, ShareState
from ..models.user import User
from ..services import download_token as download_token_svc
from ..services import file as file_svc
from ..services import share as share_svc
from ..services.audit import record_audit_event
from ..utils.ua_fingerprint import ua_fingerprint_hash

logger = logging.getLogger("fileheron.files")

router = APIRouter(prefix="/api/files", tags=["files"])

# The actual GET /download endpoint goes on a SEPARATE router that's
# mounted WITHOUT the require_2fa_complete dependency in main.py. The
# gate's get_actor() requires the Authorization header, which the
# browser-driven `<a href>` flow can't provide. Auth on this route
# happens via the signed `?dt=` token (browser path) or the bearer
# header (curl path); 2FA enforcement for the bearer path is done
# inline below.
download_router = APIRouter(prefix="/api/files", tags=["files-download"])


def _get_file_or_404(db: Session, file_id: str) -> File:
    file = db.query(File).filter(File.id == file_id).one_or_none()
    if file is None:
        raise AppError(404, "FILE_NOT_FOUND", "File not found.")
    return file


def _resolve_download_user(
    request: Request,
    db: Session,
    file_id: str,
    dt: str | None,
    authorization: str | None,
) -> User:
    """Two auth paths:

    - Browser-driven `<a href>` downloads can't carry the bearer
      token (lives in memory, not a cookie). They get a one-shot
      signed URL via /download-url and pass `?dt=<token>` here.
      The mint endpoint already enforced 2FA, so the signed URL
      is a token of past 2FA — no further check needed here.
    - API / curl callers continue to use the bearer header. For
      session-authed bearer (JWT, not API token), enforce 2FA
      inline so the same policy applies as for other gated
      routes — this router skips the global gate because the
      gate requires bearer.
    """
    if dt:
        user_id = download_token_svc.verify(file_id, dt)
        user = (
            db.query(User)
            .filter(User.id == user_id, User.is_disabled.is_(False))
            .one_or_none()
        )
        if user is None:
            raise AppError(
                401, "INVALID_DOWNLOAD_TOKEN", "Bad download token."
            )
        request.state.user_id = user.id
        request.state.auth_via = "download_token"
        return user
    # Bearer path (JWT or API token).
    user = get_actor(request=request, authorization=authorization, db=db)
    # Mirror the global require_2fa_complete gate for JWT users —
    # API tokens are session-less and trusted-at-issuance per the
    # existing convention.
    if getattr(request.state, "auth_via", "") == "session":
        from ..services.twofa_policy import is_2fa_required
        if is_2fa_required(db, user):
            raise AppError(
                403,
                "TWOFA_SETUP_REQUIRED",
                "Two-factor authentication is required. Set it up to continue.",
            )
    return user


@router.get("/{file_id}/download-url")
def get_download_url(
    file_id: str,
    user: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    """Mint a short-lived signed URL the SPA can pass to
    window.location.href. Pre-flights the same access checks the
    download endpoint runs, so the user gets a clean 4xx now rather
    than a confusing browser error after navigation."""
    file = _get_file_or_404(db, file_id)
    share = db.query(Share).filter(Share.id == file.share_id).one()
    if not share_svc.is_authorized_to_download(db, user=user, share=share):
        raise AppError(403, "FORBIDDEN", "You don't have access to this file.")
    if file.state == FileState.uploading:
        raise AppError(409, "STILL_UPLOADING", "File hasn't finished uploading yet.")
    if file.state == FileState.deleted:
        raise AppError(410, "FILE_DELETED", "File has been deleted.")
    if file.state == FileState.infected:
        raise AppError(410, "FILE_INFECTED", "File was quarantined.")
    if file.state == FileState.ready_unscanned:
        raise AppError(
            425, "SCAN_IN_PROGRESS", "Antivirus scan still in progress; try again shortly."
        )
    token = download_token_svc.issue(file_id, user.id)
    return {"url": f"/api/files/{file_id}/download?dt={token}"}


@download_router.get("/{file_id}/download")
def download_file(
    file_id: str,
    request: Request,
    db: Session = Depends(get_db),
    dt: str | None = None,
    authorization: str | None = Header(default=None),
) -> FileResponse:
    user = _resolve_download_user(request, db, file_id, dt, authorization)
    file = _get_file_or_404(db, file_id)
    share = db.query(Share).filter(Share.id == file.share_id).one()

    if not share_svc.is_authorized_to_download(db, user=user, share=share):
        raise AppError(403, "FORBIDDEN", "You don't have access to this file.")

    if file.state == FileState.uploading:
        raise AppError(409, "STILL_UPLOADING", "File hasn't finished uploading yet.")
    if file.state == FileState.deleted:
        raise AppError(410, "FILE_DELETED", "File has been deleted.")
    if file.state == FileState.infected:
        raise AppError(410, "FILE_INFECTED", "File was quarantined.")
    if file.state == FileState.ready_unscanned:
        # Phase 5: block downloads until the AV worker has cleared the file.
        raise AppError(
            425, "SCAN_IN_PROGRESS", "Antivirus scan still in progress; try again shortly."
        )

    if not file.storage_path or not Path(file.storage_path).is_file():
        logger.error("file %s has missing storage_path: %r", file.id, file.storage_path)
        raise AppError(500, "STORAGE_MISSING", "File data is missing on disk.")

    # Log the download.
    via = DownloadVia.api_token if getattr(request.state, "auth_via", "") == "api_token" else DownloadVia.auth
    db.add(
        DownloadLog(
            file_id=file.id,
            share_id=file.share_id,
            accessed_by_user_id=user.id,
            ip=(request.client.host if request.client else None),
            ua_fingerprint_hash=ua_fingerprint_hash(request.headers.get("user-agent", "")),
            bytes_served=file.size_bytes,
            via=via,
        )
    )
    record_audit_event(
        db,
        event_type=AuditEventType.file_downloaded,
        actor_user_id=user.id,
        target_type="file",
        target_id=file.id,
        metadata={"via": via.value, "share_id": file.share_id},
        request=request,
    )
    db.commit()

    # FastAPI's FileResponse uses os.sendfile under uvicorn — kernel-level
    # zero-copy from the disk fd to the socket. The Content-Disposition
    # forces a download dialog; the original filename is sanitized via
    # python's email.utils.format_addr-ish quoting that FileResponse does.
    return FileResponse(
        path=file.storage_path,
        media_type=file.mime_type,
        filename=file.original_filename,
        # Force download (don't render in-browser).
        content_disposition_type="attachment",
    )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(
    file_id: str,
    request: Request,
    user: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> None:
    file = _get_file_or_404(db, file_id)
    if file.uploaded_by_id != user.id and user.role.value != "admin":
        raise AppError(403, "FORBIDDEN", "Only the uploader or an admin can delete this file.")
    if file.state == FileState.deleted:
        return  # idempotent
    if file.state == FileState.infected and user.role.value != "admin":
        raise AppError(
            403,
            "FILE_QUARANTINED_ADMIN_ONLY",
            "Antivirus flagged this file. Only an administrator can act on it "
            "via /admin/quarantine (release back, purge, or download).",
        )
    share_id = file.share_id
    file_svc.hard_delete(db, file=file, reason="user_request", request=request)

    # If this was the last non-deleted file in an active share, the share
    # is now functionally useless — auto-revoke it. Audit metadata
    # distinguishes this path from manual revoke or AV-triggered revoke.
    share = db.query(Share).filter(Share.id == share_id).one_or_none()
    if share is not None and share.state == ShareState.active:
        remaining = (
            db.query(File)
            .filter(
                File.share_id == share_id,
                File.state != FileState.deleted,
                File.id != file.id,
            )
            .count()
        )
        if remaining == 0:
            share.state = ShareState.revoked
            record_audit_event(
                db,
                event_type=AuditEventType.share_revoked,
                actor_user_id=user.id,
                target_type="share",
                target_id=share.id,
                metadata={"reason": "last_file_deleted"},
                request=request,
            )

    db.commit()
