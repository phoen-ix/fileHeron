"""Anonymous public-share routes mounted at /d/{token}.

Three endpoints, no auth:
- GET  /d/{token}                              — landing metadata
- POST /d/{token}/unlock                       — password unlock
- GET  /d/{token}/files/{file_id}/download     — actual file fetch

Unlock flow:
- If the link has a password, /unlock checks it and sets a short-lived
  signed cookie scoped to /d/{token}. The cookie payload is an HMAC of
  (link_id, exp) under JWT_SECRET — no DB lookup on subsequent download
  requests (the cookie itself is the proof).
- The cookie expires when the share expires or in 24h, whichever comes
  first.
- If the link has no password, downloads are open immediately.

Counter decrement happens on the actual download request, atomically.
"""
from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..dependencies import get_db
from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.download_log import DownloadLog, DownloadVia
from ..models.file import File, FileState
from ..models.public_link import PublicLink
from ..models.share import Share
from ..schemas.public_link import (
    PublicShareFile,
    PublicShareResponse,
    UnlockPublicLinkRequest,
    UnlockPublicLinkResponse,
)
from ..services import public_link as public_link_svc
from ..services.audit import record_audit_event
from ..utils.ua_fingerprint import ua_fingerprint_hash

logger = logging.getLogger("fileheron.public")

router = APIRouter(prefix="/api/public", tags=["public"])

UNLOCK_COOKIE = "fh_dl_unlock"
UNLOCK_TTL_SEC = 24 * 60 * 60  # 24h
# The cookie's `path` scope must match the actual API path so the browser
# attaches it to subsequent download GETs.
COOKIE_PATH_PREFIX = "/api/public"


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _make_unlock_cookie(link_id: str, expires_at: datetime) -> str:
    payload = json.dumps(
        {"link_id": link_id, "exp": int(expires_at.timestamp())},
        separators=(",", ":"),
    ).encode("utf-8")
    sig = hmac_mod.new(
        settings.JWT_SECRET.encode("utf-8"), payload, hashlib.sha256
    ).digest()
    return f"{_b64url(payload)}.{_b64url(sig)}"


def _verify_unlock_cookie(value: str, link_id: str) -> bool:
    try:
        payload_b64, sig_b64 = value.split(".", 1)
        payload = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
    except (ValueError, base64.binascii.Error):
        return False
    expected = hmac_mod.new(
        settings.JWT_SECRET.encode("utf-8"), payload, hashlib.sha256
    ).digest()
    if not hmac_mod.compare_digest(expected, sig):
        return False
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if data.get("link_id") != link_id:
        return False
    exp = int(data.get("exp", 0))
    return exp > int(datetime.now(tz=timezone.utc).timestamp())


def _is_unlocked(link: PublicLink, cookie_value: str | None) -> bool:
    if link.password_hash is None:
        return True
    if not cookie_value:
        return False
    return _verify_unlock_cookie(cookie_value, link.id)


@router.get("/{token}", response_model=PublicShareResponse)
def landing(
    token: str,
    db: Session = Depends(get_db),
    fh_dl_unlock: str | None = Cookie(default=None),
) -> PublicShareResponse:
    link = public_link_svc.get_link_by_token(db, token)
    public_link_svc.assert_link_usable(db, link)
    share = db.query(Share).filter(Share.id == link.share_id).one()
    files = (
        db.query(File)
        .filter(File.share_id == share.id, File.state != FileState.deleted)
        .all()
    )
    return PublicShareResponse(
        share_id=share.id,
        subject=share.subject,
        message=share.message,
        expires_at=share.expires_at,
        requires_password=link.password_hash is not None,
        unlocked=_is_unlocked(link, fh_dl_unlock),
        downloads_remaining=link.downloads_remaining,
        files=[
            PublicShareFile(
                id=f.id,
                original_filename=f.original_filename,
                mime_type=f.mime_type,
                size_bytes=f.size_bytes,
                state=f.state.value,
            )
            for f in files
        ],
    )


@router.post("/{token}/unlock", response_model=UnlockPublicLinkResponse)
def unlock(
    token: str,
    payload: UnlockPublicLinkRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> UnlockPublicLinkResponse:
    link = public_link_svc.get_link_by_token(db, token)
    public_link_svc.assert_link_usable(db, link)
    if link.password_hash is None:
        return UnlockPublicLinkResponse(ok=True)

    ip = request.client.host if request.client else None
    ok = public_link_svc.verify_password(db, link=link, password=payload.password, ip=ip)
    db.commit()
    if not ok:
        raise AppError(401, "INVALID_PUBLIC_PASSWORD", "Incorrect password.")

    share = db.query(Share).filter(Share.id == link.share_id).one()
    cookie_exp = min(
        _utcnow() + timedelta(seconds=UNLOCK_TTL_SEC),
        share.expires_at,
    )
    cookie_value = _make_unlock_cookie(link.id, cookie_exp)
    response.set_cookie(
        key=UNLOCK_COOKIE,
        value=cookie_value,
        max_age=UNLOCK_TTL_SEC,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        # Scope to this link's API path so unlocking one share doesn't
        # leak to another. The SPA is at PUBLIC_LINK_BASE_PATH ("/d");
        # the cookie rides only on the matching /api/public/{token} XHR.
        path=f"{COOKIE_PATH_PREFIX}/{token}",
    )
    return UnlockPublicLinkResponse(ok=True)


@router.get("/{token}/files/{file_id}/download")
def public_download(
    token: str,
    file_id: str,
    request: Request,
    db: Session = Depends(get_db),
    fh_dl_unlock: str | None = Cookie(default=None),
) -> FileResponse:
    link = public_link_svc.get_link_by_token(db, token)
    public_link_svc.assert_link_usable(db, link)

    if not _is_unlocked(link, fh_dl_unlock):
        raise AppError(401, "UNLOCK_REQUIRED", "Submit the password first.")

    file = db.query(File).filter(File.id == file_id).one_or_none()
    if file is None or file.share_id != link.share_id:
        raise AppError(404, "FILE_NOT_FOUND", "File not found in this public share.")
    if file.state == FileState.uploading:
        raise AppError(409, "STILL_UPLOADING", "File hasn't finished uploading yet.")
    if file.state == FileState.ready_unscanned:
        raise AppError(
            425, "SCAN_IN_PROGRESS", "Antivirus scan still in progress; try again shortly."
        )
    if file.state == FileState.infected:
        raise AppError(410, "FILE_INFECTED", "File was quarantined.")
    if file.state == FileState.deleted:
        raise AppError(410, "FILE_DELETED", "File has been deleted.")

    # Counter (atomic). On success, `downloads_remaining` reflects the
    # post-decrement value used by the owner notification below.
    allowed, downloads_remaining = public_link_svc.decrement_counter(db, link=link)
    if not allowed:
        db.commit()
        raise AppError(
            410, "PUBLIC_LINK_EXHAUSTED", "This public link's download limit has been reached."
        )

    if not file.storage_path or not Path(file.storage_path).is_file():
        logger.error("public download: storage missing for %s", file.id)
        raise AppError(500, "STORAGE_MISSING", "File data is missing on disk.")

    ip = request.client.host if request.client else None
    db.add(
        DownloadLog(
            file_id=file.id,
            share_id=file.share_id,
            accessed_by_user_id=None,
            ip=ip,
            ua_fingerprint_hash=ua_fingerprint_hash(request.headers.get("user-agent", "")),
            bytes_served=file.size_bytes,
            via=DownloadVia.public,
        )
    )
    record_audit_event(
        db,
        event_type=AuditEventType.file_downloaded,
        actor_user_id=None,
        target_type="file",
        target_id=file.id,
        metadata={"via": "public", "share_id": file.share_id, "public_link_id": link.id},
        request=request,
    )
    public_link_svc.record_consumption(
        db, link=link, file_id=file.id, ip=ip, request=request
    )
    public_link_svc.notify_owner_on_download(
        db, link=link, file=file, downloads_remaining=downloads_remaining
    )

    db.commit()

    return FileResponse(
        path=file.storage_path,
        media_type=file.mime_type,
        filename=file.original_filename,
        content_disposition_type="attachment",
    )
