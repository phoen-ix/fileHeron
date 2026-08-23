"""Anonymous public-share routes mounted at /d/{token}.

Three endpoints, no auth:
- GET  /d/{token}                              - landing metadata
- POST /d/{token}/unlock                       - password unlock
- GET  /d/{token}/files/{file_id}/download     - actual file fetch

Unlock flow:
- If the link has a password, /unlock checks it and sets a short-lived
  signed cookie scoped to /d/{token}. The cookie payload is an HMAC of
  (link_id, exp) under JWT_SECRET - no DB lookup on subsequent download
  requests (the cookie itself is the proof).
- The cookie expires when the share expires or in 24h, whichever comes
  first.
- If the link has no password, downloads are open immediately.

Counter decrement happens on the actual download request, atomically.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac as hmac_mod
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, Header, Request, Response
from sqlalchemy.orm import Session

from ..config import settings
from ..dependencies import get_db
from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.download_log import DownloadLog, DownloadVia
from ..models.file import File, FileApprovalState, FileState
from ..models.public_link import PublicLink
from ..models.share import Share
from ..schemas.public_link import (
    PublicShareFile,
    PublicShareResponse,
    UnlockPublicLinkRequest,
    UnlockPublicLinkResponse,
)
from ..services import file as file_svc
from ..services import public_link as public_link_svc
from ..services import settings as settings_svc
from ..services import transfer_activity
from ..services import zip_stream as zip_stream_svc
from ..services.audit import record_audit_event
from ..services.storage_backend import get_storage_backend
from ..utils.columns import declared_width
from ..utils.crypto import constant_time_equals
from ..utils.http_range import (
    UnsatisfiableRangeError,
    is_metadata_probe,
    is_partial_continuation,
    parse_single_range,
)
from ..utils.timeutil import to_epoch, utc_now
from ..utils.ua_fingerprint import ua_fingerprint_hash

# See routers/files.py: same column, same caller-controlled source. These are
# the ANONYMOUS download paths, so the value is one an unauthenticated caller
# can influence directly on an edge that appends to X-Forwarded-For.
_DOWNLOAD_IP_MAX = declared_width(DownloadLog.__table__.c.ip)

logger = logging.getLogger("fileheron.public")

router = APIRouter(prefix="/api/public", tags=["public"])

UNLOCK_COOKIE = "fh_dl_unlock"
UNLOCK_TTL_SEC = 24 * 60 * 60  # 24h
# The cookie's `path` scope must match the actual API path so the browser
# attaches it to subsequent download GETs.
COOKIE_PATH_PREFIX = "/api/public"




def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _make_unlock_cookie(link_id: str, expires_at: datetime) -> str:
    # expires_at is naive UTC (the storage convention); .timestamp() on a naive
    # datetime is interpreted in the process-local TZ, so stamp UTC explicitly to
    # match _verify_unlock_cookie's aware-UTC comparison below.
    payload = json.dumps(
        {"link_id": link_id, "exp": int(expires_at.replace(tzinfo=timezone.utc).timestamp())},
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
    except (ValueError, binascii.Error):
        return False
    expected = hmac_mod.new(
        settings.JWT_SECRET.encode("utf-8"), payload, hashlib.sha256
    ).digest()
    if not constant_time_equals(expected, sig):
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
        .filter(
            File.share_id == share.id,
            File.state != FileState.deleted,
            # Not even the NAME of a file awaiting review: this listing is
            # anonymous, and the point of the gate is that nobody outside the
            # owner/approver pair learns about the content until it is released.
            File.approval_state == FileApprovalState.approved,
        )
        .all()
    )
    # A password-protected link that has NOT been unlocked yet must not disclose
    # what it is protecting. `unlocked` was computed and reported, but the
    # subject, the sender's message and the entire file list (names, types,
    # sizes) were returned regardless - so the password gated the bytes while
    # anyone holding the URL could read the metadata, which is often the
    # sensitive part: "Q3-layoffs-list.xlsx" discloses plenty on its own
    # (audit 2026-07-30). Expiry, the requires_password flag and
    # downloads_remaining stay visible: the unlock screen needs them.
    unlocked = _is_unlocked(link, fh_dl_unlock)
    gated = link.password_hash is not None and not unlocked
    if gated:
        files = []

    return PublicShareResponse(
        share_id=share.id,
        subject=None if gated else share.subject,
        message=None if gated else share.message,
        expires_at=share.expires_at,
        requires_password=link.password_hash is not None,
        unlocked=unlocked,
        downloads_remaining=link.downloads_remaining,
        preview_enabled=settings_svc.get_bool(
            db, settings_svc.Keys.FILE_PREVIEW_ENABLED, default=True
        ),
        files=[
            PublicShareFile(
                id=f.id,
                original_filename=f.original_filename,
                mime_type=f.mime_type,
                size_bytes=f.size_bytes,
                state=f.state.value,
                av_unscanned=f.av_unscanned,
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
    # Serialize the check-and-record for this link: without the row lock a
    # concurrent burst of guesses could all read the attempt count below the
    # threshold and pass BEFORE any of them recorded its attempt (TOCTOU),
    # bypassing the per-IP / link-wide brute-force cap. The lock is held until
    # the commit below. (SELECT ... FOR UPDATE - a no-op on SQLite/tests, real
    # on MariaDB; mirrors rate_limit.record_failure.)
    db.refresh(link, with_for_update=True)
    # Per-IP throttle: a single noisy IP is refused here (429) without
    # locking the link for legitimate users on other IPs (finding M5).
    if public_link_svc.ip_is_rate_limited(db, link, ip):
        raise AppError(
            429,
            "PUBLIC_LINK_RATE_LIMITED",
            "Too many failed attempts from your address; try again later.",
        )
    ok = public_link_svc.verify_password(db, link=link, password=payload.password, ip=ip)
    db.commit()
    if not ok:
        raise AppError(401, "INVALID_PUBLIC_PASSWORD", "Incorrect password.")

    share = db.query(Share).filter(Share.id == link.share_id).one()
    # Cap the unlock cookie at the share's expiry so a leaked cookie
    # can't outlive the share itself. NULL expires_at = never-expire
    # (v1.1.4) - in that case the share doesn't bound the cookie, so
    # the UNLOCK_TTL_SEC max-age (24h) is the only ceiling.
    base_exp = utc_now() + timedelta(seconds=UNLOCK_TTL_SEC)
    cookie_exp = base_exp if share.expires_at is None else min(base_exp, share.expires_at)
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
) -> Response:
    link = public_link_svc.get_link_by_token(db, token)

    # A Range continuation of the download that consumed the last unit must be
    # allowed to finish (the counter already hit 0); it isn't re-counted below.
    #
    # But "continuation" has to be CORROBORATED, not just claimed. The header
    # test alone is "does Range start above byte 0", so `Range: bytes=1-` on a
    # brand-new connection got both the exhausted-check waiver and the free
    # ride: a link holder could re-download every file, unlimited times, with
    # `downloads_remaining` never moving and no download_log row, no audit
    # entry and no owner notification (audit 2026-07-30, flow-publiclink-7).
    #
    # The evidence is that THIS LINK already PAID for this file inside the
    # window - not that this instance served it recently.
    #
    # v2.6.0 used the serving mark (`was_download_recent`), which answers the
    # question the maintenance drain asks, not the one a budget asks. The
    # difference was reachable three ways: the share owner previewing their own
    # file wrote that mark and handed every link holder unlimited free copies;
    # the authenticated and public ZIP routes derived the same key and
    # corroborated each other across the auth boundary; and because the mark was
    # written wherever bytes were served, a free continuation refreshed it, so
    # the window renewed itself indefinitely while the comment here claimed it
    # was bounded.
    #
    # Keyed on (link, file) rather than the client, so a phone changing networks
    # mid-download keeps its continuation. Fails CLOSED when Redis is down: this
    # comment promised the opposite until audit #2, and while a refused resume
    # is annoying, the other direction meant that for the whole duration of an
    # outage a spent link served the file to anyone sending `Range: bytes=1-`,
    # repeatedly, with the counter unmoved and nothing written down. The
    # residual is what the old comment claimed: one payment buys free
    # continuations for one window, and cannot extend itself, because only the
    # payment path marks.
    paid_key = f"link:{link.id}:file:{file_id}"
    is_continuation = is_partial_continuation(
        request
    ) and transfer_activity.was_download_paid(paid_key)

    public_link_svc.assert_link_usable(
        db, link, allow_exhausted_continuation=is_continuation
    )

    if not _is_unlocked(link, fh_dl_unlock):
        raise AppError(401, "UNLOCK_REQUIRED", "Submit the password first.")

    from ..services import maintenance as maintenance_svc
    maintenance_svc.refuse_if_maintenance(
        db, request=request, kind="download", file_id=file_id
    )

    file = db.query(File).filter(File.id == file_id).one_or_none()
    # A file still awaiting its own four-eyes decision is not part of this link.
    # 404 rather than 409: an anonymous holder is neither the owner nor an
    # approver, so the only thing a distinct code would tell them is that
    # unreleased content exists.
    if (
        file is None
        or file.share_id != link.share_id
        or file.approval_state != FileApprovalState.approved
    ):
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

    backend = get_storage_backend()
    if not file.storage_path or not backend.exists(file.storage_path):
        logger.error("public download: storage missing for %s", file.id)
        raise AppError(500, "STORAGE_MISSING", "File data is missing.")

    # A size probe is not a download - see utils/http_range.is_metadata_probe.
    # The counter and the log only: `assert_link_usable` above already refused a
    # spent link, so a probe cannot be used to confirm a link is still live
    # after its budget is gone.
    is_probe = is_metadata_probe(request.headers.get("range"), file.size_bytes)

    # Parallel/segmented downloads send several ranged GETs for one logical
    # download; the byte-0 (or full) request counts it + logs, the continuation
    # ranges must not re-decrement or re-log. See utils/http_range - and note
    # this uses the CORROBORATED `is_continuation` computed above, not the bare
    # header test.
    if not is_continuation and not is_probe:
        # Counter (atomic). On success, `downloads_remaining` reflects the
        # post-decrement value used by the owner notification below.
        allowed, downloads_remaining = public_link_svc.decrement_counter(db, link=link)
        if not allowed:
            db.commit()
            raise AppError(
                410, "PUBLIC_LINK_EXHAUSTED", "This public link's download limit has been reached."
            )
        # Paid. This is the only place the continuation mark is written, which
        # is what keeps the free window bounded: a continuation never reaches
        # here, so it cannot renew its own licence.
        transfer_activity.mark_download_paid(paid_key)

        ip = request.client.host[:_DOWNLOAD_IP_MAX] if request.client else None
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

    from ..services import settings_registry
    from ..services.storage_backend import serve_response
    ttl = settings_registry.effective(db, settings_registry.K.DOWNLOAD_SIGNED_URL_TTL_SEC)
    return serve_response(
        backend,
        locator=file.storage_path,
        filename=file.original_filename,
        mime_type=file.mime_type,
        ttl_sec=ttl,
        count=True,
        file_id=file.id,
    )


@router.get("/{token}/files/{file_id}/preview")
def public_preview(
    token: str,
    file_id: str,
    request: Request,
    db: Session = Depends(get_db),
    fh_dl_unlock: str | None = Cookie(default=None),
) -> Response:
    """Serve a public-share file INLINE for in-browser preview. Same unlock gate
    as the download path, but **never** decrements the link counter, writes a
    `download_log` row, records consumption, or notifies the owner - preview is
    "look". An exhausted link serves neither download nor preview (410). Bytes
    are served with a server-chosen safe Content-Type + nosniff/CSP hardening."""
    from ..services import preview as preview_svc
    from ..services import settings_registry
    from ..services.storage_backend import serve_response

    link = public_link_svc.get_link_by_token(db, token)
    public_link_svc.assert_link_usable(db, link)
    if not _is_unlocked(link, fh_dl_unlock):
        raise AppError(401, "UNLOCK_REQUIRED", "Submit the password first.")
    from ..services import maintenance as maintenance_svc
    maintenance_svc.refuse_if_maintenance(
        db, request=request, kind="download", file_id=file_id
    )
    if not settings_svc.get_bool(
        db, settings_svc.Keys.FILE_PREVIEW_ENABLED, default=True
    ):
        raise AppError(403, "PREVIEW_DISABLED", "In-browser preview is disabled.")

    file = db.query(File).filter(File.id == file_id).one_or_none()
    # A file still awaiting its own four-eyes decision is not part of this link.
    # 404 rather than 409: an anonymous holder is neither the owner nor an
    # approver, so the only thing a distinct code would tell them is that
    # unreleased content exists.
    if (
        file is None
        or file.share_id != link.share_id
        or file.approval_state != FileApprovalState.approved
    ):
        raise AppError(404, "FILE_NOT_FOUND", "File not found in this public share.")
    if file.state == FileState.uploading:
        raise AppError(409, "STILL_UPLOADING", "File hasn't finished uploading yet.")
    if file.state == FileState.ready_unscanned:
        raise AppError(
            425, "SCAN_IN_PROGRESS", "Antivirus scan still in progress; try again shortly."
        )
    if file.state == FileState.infected:
        raise AppError(410, "FILE_INFECTED", "File was quarantined.")
    if file.av_unscanned:
        # `clean` here means "no verdict", not "a clean verdict" - clamd clamps
        # MaxFileSize to ~2 GiB, so anything larger is served with an
        # `unscanned` badge and never opened by the scanner. Downloading it is
        # the visitor's informed choice; rendering it INLINE into their PDF
        # viewer, one anonymous click from a public link, is not (audit #2).
        raise AppError(
            409,
            "FILE_NOT_SCANNED",
            "This file was too large to scan for viruses, so it can't be "
            "previewed in the browser. Download it and check it locally.",
        )
    if file.state == FileState.deleted:
        raise AppError(410, "FILE_DELETED", "File has been deleted.")
    if not preview_svc.is_previewable(file.mime_type):
        raise AppError(415, "PREVIEW_UNSUPPORTED", "This file type can't be previewed.")
    if link.download_limit is not None and (link.downloads_remaining or 0) <= 0:
        raise AppError(
            410, "PUBLIC_LINK_EXHAUSTED", "This public link's download limit has been reached."
        )

    backend = get_storage_backend()
    if not file.storage_path or not backend.exists(file.storage_path):
        logger.error("public preview: storage missing for %s", file.id)
        raise AppError(500, "STORAGE_MISSING", "File data is missing.")

    # Preview is free of charge by design - it does not decrement the download
    # budget - but it still hands an anonymous caller the COMPLETE original
    # bytes. Nothing anywhere recorded that: no download_log row, no audit
    # entry. So a share could be exfiltrated in full through the preview route
    # and neither the owner nor an investigator would find any trace that it had
    # left the server, while the download counter still read zero
    # (audit 2026-07-30). Range continuations are skipped so a PDF viewer
    # fetching in chunks does not write a row per chunk.
    #
    # That skip was a BARE header test, which is the defect v2.6.0 closed
    # everywhere it governed a counter and missed here, where it governs the
    # only forensic record this route produces. `Range: bytes=1-` on each
    # request fetched every previewable file - images, PDFs, any text/* - and
    # left nothing behind at all.
    #
    # The evidence direction is the opposite of the budget's, and deliberately
    # so. A budget must fail CLOSED (no proof of payment means charge), so it
    # asks "did this link already pay". An audit trail must fail OPEN in the
    # other sense: when in doubt, WRITE the row. A duplicate audit entry costs
    # a little noise; a missing one is the whole point of the control. So the
    # exemption here is narrow - only a real continuation of a preview this
    # link has already been recorded for, and only a request that is not
    # taking the file (a one-byte metadata probe is not a preview).
    already_traced = transfer_activity.was_download_paid(
        f"link:{link.id}:preview:{file.id}"
    )
    is_traced_continuation = is_partial_continuation(request) and already_traced
    if not is_traced_continuation:
        record_audit_event(
            db,
            event_type=AuditEventType.public_link_previewed,
            actor_user_id=None,
            target_type="public_link",
            target_id=link.id,
            metadata={
                "file_id": file.id,
                "share_id": link.share_id,
                "bytes": file.size_bytes,
            },
            request=request,
        )
        db.commit()
        # Only the traced path marks, so a continuation cannot extend its own
        # exemption - the same rule the budget mark follows.
        transfer_activity.mark_download_paid(f"link:{link.id}:preview:{file.id}")

    ttl = settings_registry.effective(db, settings_registry.K.DOWNLOAD_SIGNED_URL_TTL_SEC)
    return serve_response(
        backend,
        locator=file.storage_path,
        filename=file.original_filename,
        mime_type=preview_svc.safe_content_type(file.mime_type),
        ttl_sec=ttl,
        disposition="inline",
        extra_headers=preview_svc.SECURITY_HEADERS,
        count=True,
        file_id=file.id,
    )


@router.get("/{token}/download-zip")
def public_download_zip(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
    fh_dl_unlock: str | None = Cookie(default=None),
    range_header: str | None = Header(default=None, alias="Range"),
    if_range: str | None = Header(default=None, alias="If-Range"),
) -> Response:
    """Stream a single ZIP of every downloadable file behind a public link.
    Same unlock gate as the single-file path; ONE ZIP counts as ONE download
    against the link budget, and a genuine resume of it is free.

    Resumable since v2.6.0. Before that a 9 GB archive that died at 90% was
    unrecoverable: the link had already been charged, every retry restarted at
    byte 0, and once the budget ran out the retries got 410 (audit 2026-07-30,
    flow-publiclink-5). The archive is reproducible and seekable, so this now
    answers a Range with a real 206."""
    link = public_link_svc.get_link_by_token(db, token)
    # The bare header claim, used ONLY to decide whether an exhausted link may
    # proceed far enough to compute the archive's identity (the corroborating
    # evidence is keyed on the ETag, so it cannot be checked this early). The
    # authoritative decision is the `corroborated` boolean below: a fabricated
    # `Range: bytes=1-` on a spent link reaches the atomic decrement and gets
    # the same 410, just a few stat calls later.
    public_link_svc.assert_link_usable(
        db, link, allow_exhausted_continuation=is_partial_continuation(request)
    )

    if not _is_unlocked(link, fh_dl_unlock):
        raise AppError(401, "UNLOCK_REQUIRED", "Submit the password first.")

    from ..services import maintenance as maintenance_svc
    files = file_svc.downloadable_files(db, link.share_id)
    if not files:
        raise AppError(400, "NO_DOWNLOADABLE_FILES", "This share has no downloadable files.")

    share = db.query(Share).filter(Share.id == link.share_id).one()
    # The timestamp comes from the share, never the clock, so the same member
    # list always produces the same bytes; the ETag covers that list, so a file
    # quarantined mid-transfer makes `If-Range` miss and the client restarts
    # cleanly instead of splicing two different archives together.
    mtime = to_epoch(share.created_at)
    etag, total = zip_stream_svc.zip_identity(files, mtime=mtime)
    quoted_etag = f'"{etag}"'

    byte_range = None
    if if_range is None or if_range.strip() == quoted_etag:
        try:
            parsed = parse_single_range(range_header, total)
        except UnsatisfiableRangeError:
            # RFC 9110 15.5.17: the 416 carries the resource length so the
            # client can recover. A bare Response rather than the usual error
            # envelope because Content-Range is the entire point of the reply.
            return Response(
                status_code=416, headers={"Content-Range": f"bytes */{total}"}
            )
        byte_range = (parsed.start, parsed.end) if parsed else None

    # A resume is free; a CLAIM of one is not. Evidence is that THIS LINK
    # already paid for this exact archive inside the window - keyed on the link
    # AND the ETag, so a Range against a changed member list is a new download
    # and pays like one. Without this, `Range: bytes=1-` would be an unlimited
    # free-download bypass, which is the defect the old always-charge rule was
    # avoiding by refusing to resume at all.
    #
    # The link id is load-bearing. v2.6.0 keyed this on `zip:{share_id}:{etag}`
    # and corroborated it with the SERVING mark - and the authenticated ZIP
    # route derives an identical key from the same reproducible archive
    # identity, so an owner downloading their own archive silently authorised
    # unlimited anonymous ones. Different principals, one key.
    paid_key = f"link:{link.id}:zip:{etag}"
    resuming = byte_range is not None and byte_range[0] > 0
    corroborated = resuming and transfer_activity.was_download_paid(paid_key)

    # Maintenance is refused HERE, after the archive identity is known, so a
    # CORROBORATED resume can finish - the same ordering the authenticated ZIP
    # route uses. The gate used to sit at the top with a comment explaining that
    # a ZIP "hands back the whole archive whatever the Range header says", which
    # stopped being true in v2.7.0; the sibling fix retired that reasoning and
    # this copy was missed (audit #2 cross-check). A resume with no payment
    # behind it is still a new transfer and is still refused.
    if not corroborated:
        maintenance_svc.refuse_if_maintenance(db, kind="download")

    downloads_remaining = link.downloads_remaining
    if not corroborated:
        allowed, downloads_remaining = public_link_svc.decrement_counter(db, link=link)
        if not allowed:
            db.commit()
            raise AppError(
                410,
                "PUBLIC_LINK_EXHAUSTED",
                "This public link's download limit has been reached.",
            )
        # Only the payment path marks, so a free continuation cannot renew it.
        transfer_activity.mark_download_paid(paid_key)

        ip = request.client.host[:_DOWNLOAD_IP_MAX] if request.client else None
        ua = ua_fingerprint_hash(request.headers.get("user-agent", ""))
        for f in files:
            db.add(
                DownloadLog(
                    file_id=f.id,
                    share_id=link.share_id,
                    accessed_by_user_id=None,
                    ip=ip,
                    ua_fingerprint_hash=ua,
                    bytes_served=f.size_bytes,
                    via=DownloadVia.public,
                )
            )
        record_audit_event(
            db,
            event_type=AuditEventType.share_downloaded,
            actor_user_id=None,
            target_type="share",
            target_id=link.share_id,
            metadata={
                "via": "public",
                "file_count": len(files),
                "archive": True,
                "public_link_id": link.id,
            },
            request=request,
        )
        public_link_svc.record_consumption(
            db, link=link, file_id=None, ip=ip, request=request
        )
        public_link_svc.notify_owner_on_archive_download(
            db,
            link=link,
            file_count=len(files),
            total_bytes=sum(f.size_bytes for f in files),
            downloads_remaining=downloads_remaining,
        )
        db.commit()

    return zip_stream_svc.zip_streaming_response(
        files,
        f"share-{share.id[:8]}",
        count=True,
        mtime=mtime,
        byte_range=byte_range,
        etag=etag,
    )
