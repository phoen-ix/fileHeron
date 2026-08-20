"""/api/files/* - download a file (auth-gated) and delete a file."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..dependencies import get_actor, get_db, request_has_scope, require_scope
from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.download_log import DownloadLog, DownloadVia
from ..models.file import File, FileState
from ..models.share import Share, ShareState
from ..models.user import User, UserRole
from ..schemas.common import SignedUrlResponse
from ..services import download_token as download_token_svc
from ..services import file as file_svc
from ..services import settings_registry as _sr
from ..services import share as share_svc
from ..services import transfer_activity
from ..services import zip_stream as zip_stream_svc
from ..services.audit import record_audit_event
from ..services.storage_backend import get_storage_backend
from ..utils.http_range import (
    UnsatisfiableRangeError,
    is_metadata_probe,
    is_partial_continuation,
    parse_single_range,
)
from ..utils.timeutil import to_epoch
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


def _assert_previewable_state(file: File) -> None:
    """The preview gate, which is stricter than the download gate.

    `av_unscanned` files are `clean` - the state means "no verdict", not "a
    clean verdict": clamd clamps MaxFileSize to ~2 GiB, so anything larger is
    marked clean-but-unscanned and served with a badge. The download path can
    do that, because the user is choosing to take a file they were told was not
    scanned. Preview is different: the bytes are rendered INLINE, into the
    browser's PDF/image viewer, on a path an anonymous public-link visitor can
    reach with one click - and the trust model written three lines above the
    previewable-type allowlist assumed clamd had read them (audit #2).
    """
    _assert_file_state_servable(file)
    if getattr(file, "av_unscanned", False):
        raise AppError(
            409,
            "FILE_NOT_SCANNED",
            "This file was too large to scan for viruses, so it can't be "
            "previewed in the browser. Download it and check it locally.",
        )


def _assert_file_state_servable(file: File) -> None:
    """Same AV/state gate the download path applies - only `clean` files are
    servable; we never hand out (or render inline) unscanned/quarantined bytes."""
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
      is a token of past 2FA - no further check needed here.
    - API / curl callers continue to use the bearer header. For
      session-authed bearer (JWT, not API token), enforce 2FA
      inline so the same policy applies as for other gated
      routes - this router skips the global gate because the
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
    # A restricted API token must hold files:download to use the bearer
    # download/preview/zip path. The ?dt= branch above is exempt: it's a token
    # of past authorization minted by an already-scope-checked *-url endpoint.
    if not request_has_scope(request, "files:download"):
        raise AppError(
            403,
            "INSUFFICIENT_SCOPE",
            "This API token lacks the required scope.",
            details={"required_scope": "files:download"},
        )
    # Mirror the global require_2fa_complete gate for JWT users -
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


@router.get("/{file_id}/download-url", response_model=SignedUrlResponse)
def get_download_url(
    file_id: str,
    user: User = Depends(require_scope("files:download")),
    db: Session = Depends(get_db),
) -> dict:
    """Mint a short-lived signed URL the SPA can pass to
    window.location.href. Pre-flights the same access checks the
    download endpoint runs, so the user gets a clean 4xx now rather
    than a confusing browser error after navigation."""
    file = _get_file_or_404(db, file_id)
    share = db.query(Share).filter(Share.id == file.share_id).one()
    share_svc.assert_share_file_access(db, user=user, share=share, file=file)
    from ..services import maintenance as maintenance_svc
    maintenance_svc.refuse_if_maintenance(db, kind="download")
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
    # v1.1.0: refuse to mint a signed URL for a share whose download
    # budget is exhausted. Saves the user from clicking a working-
    # looking URL that 410s on consume. (Race window between mint and
    # consume is still handled by the atomic decrement at the download
    # endpoint.) NULL limit = unlimited, skipped.
    if share.download_limit is not None and (share.downloads_remaining or 0) <= 0:
        raise AppError(
            410,
            "SHARE_DOWNLOAD_LIMIT_REACHED",
            "This share has reached its download limit.",
        )
    from ..services import settings_registry

    ttl = settings_registry.effective(
        db, settings_registry.K.DOWNLOAD_SIGNED_URL_TTL_SEC
    )
    token = download_token_svc.issue(file_id, user.id, ttl_sec=ttl)
    return {"url": f"/api/files/{file_id}/download?dt={token}"}


@router.get("/{file_id}/preview-url", response_model=SignedUrlResponse)
def get_preview_url(
    file_id: str,
    user: User = Depends(require_scope("files:download")),
    db: Session = Depends(get_db),
) -> dict:
    """Mint a short-lived signed URL the SPA passes to an <img>/<iframe> src (or
    fetches as text) to render a file INLINE. Same access + AV gates as the
    download mint, plus the previewable-type allowlist and the global
    file-preview kill switch. It deliberately does NOT pre-flight the download
    budget - preview never consumes it - but still refuses when the budget is
    already fully spent, so a used-up share serves neither downloads nor
    previews. Reuses the download token (same scope: this user, this file)."""
    from ..services import preview as preview_svc
    from ..services import settings as settings_svc

    file = _get_file_or_404(db, file_id)
    share = db.query(Share).filter(Share.id == file.share_id).one()
    share_svc.assert_share_file_access(db, user=user, share=share, file=file)
    from ..services import maintenance as maintenance_svc
    maintenance_svc.refuse_if_maintenance(db, kind="download")
    _assert_previewable_state(file)
    if not settings_svc.get_bool(
        db, settings_svc.Keys.FILE_PREVIEW_ENABLED, default=True
    ):
        raise AppError(403, "PREVIEW_DISABLED", "In-browser preview is disabled.")
    if not preview_svc.is_previewable(file.mime_type):
        raise AppError(415, "PREVIEW_UNSUPPORTED", "This file type can't be previewed.")
    if share.download_limit is not None and (share.downloads_remaining or 0) <= 0:
        raise AppError(
            410, "SHARE_DOWNLOAD_LIMIT_REACHED", "This share has reached its download limit."
        )

    from ..services import settings_registry
    ttl = settings_registry.effective(db, settings_registry.K.DOWNLOAD_SIGNED_URL_TTL_SEC)
    token = download_token_svc.issue(file_id, user.id, ttl_sec=ttl)
    return {"url": f"/api/files/{file_id}/preview?dt={token}"}


@download_router.get("/{file_id}/preview")
def preview_file(
    file_id: str,
    request: Request,
    db: Session = Depends(get_db),
    dt: str | None = None,
    authorization: str | None = Header(default=None),
) -> Response:
    """Serve a file INLINE for in-browser preview. Same auth + AV gates as the
    download endpoint, but **never** decrements the share download budget,
    writes a `download_log` row, or audits - preview is "look", download is
    "take". A share whose budget is fully spent serves neither (410). The bytes
    are served with a server-chosen safe Content-Type + nosniff/CSP hardening
    (see services/preview.py)."""
    from ..services import preview as preview_svc
    from ..services import settings as settings_svc
    from ..services import settings_registry
    from ..services.storage_backend import serve_response

    user = _resolve_download_user(request, db, file_id, dt, authorization)
    file = _get_file_or_404(db, file_id)
    share = db.query(Share).filter(Share.id == file.share_id).one()
    share_svc.assert_share_file_access(db, user=user, share=share, file=file)
    from ..services import maintenance as maintenance_svc
    maintenance_svc.refuse_if_maintenance(
        db, request=request, kind="download", file_id=file_id
    )
    # The gate belongs HERE, on the route that hands over the bytes. Putting it
    # only on the URL mint left the serve path unguarded, and the mint is a
    # pre-flight convenience - a `?dt=` token can be replayed straight at this
    # route (audit #2 cross-check).
    _assert_previewable_state(file)
    if not settings_svc.get_bool(
        db, settings_svc.Keys.FILE_PREVIEW_ENABLED, default=True
    ):
        raise AppError(403, "PREVIEW_DISABLED", "In-browser preview is disabled.")
    if not preview_svc.is_previewable(file.mime_type):
        raise AppError(415, "PREVIEW_UNSUPPORTED", "This file type can't be previewed.")
    if share.download_limit is not None and (share.downloads_remaining or 0) <= 0:
        raise AppError(
            410, "SHARE_DOWNLOAD_LIMIT_REACHED", "This share has reached its download limit."
        )

    backend = get_storage_backend()
    if not file.storage_path or not backend.exists(file.storage_path):
        logger.error("preview: missing storage_path for %s: %r", file.id, file.storage_path)
        raise AppError(500, "STORAGE_MISSING", "File data is missing.")

    # An APPROVER previewing a pending share is a person who is not a recipient
    # reading someone else's file. The single-file download route records that,
    # and the archive route was fixed to; preview - the third door, and the one
    # the review screen actually offers - recorded nothing at all (audit #2
    # cross-check). No budget is spent: preview is "look", and that has not
    # changed.
    if share.state == ShareState.pending_approval:
        record_audit_event(
            db,
            event_type=AuditEventType.file_downloaded,
            actor_user_id=user.id,
            target_type="file",
            target_id=file.id,
            metadata={"via": "preview", "share_id": file.share_id, "review": True},
            request=request,
        )
        db.commit()

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


@download_router.get("/{file_id}/download")
def download_file(
    file_id: str,
    request: Request,
    db: Session = Depends(get_db),
    dt: str | None = None,
    authorization: str | None = Header(default=None),
) -> Response:
    user = _resolve_download_user(request, db, file_id, dt, authorization)
    file = _get_file_or_404(db, file_id)
    share = db.query(Share).filter(Share.id == file.share_id).one()

    share_svc.assert_share_file_access(db, user=user, share=share, file=file)

    from ..services import maintenance as maintenance_svc
    maintenance_svc.refuse_if_maintenance(
        db, request=request, kind="download", file_id=file_id
    )

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

    backend = get_storage_backend()
    if not file.storage_path or not backend.exists(file.storage_path):
        logger.error("file %s has missing storage_path: %r", file.id, file.storage_path)
        raise AppError(500, "STORAGE_MISSING", "File data is missing.")

    # Parallel/segmented downloads send several ranged GETs for one logical
    # download; the byte-0 (or full) request counts it, the continuation
    # ranges must not re-decrement or re-log. See utils/http_range.
    #
    # The header alone is not proof of that. `Range: bytes=1-` on a fresh
    # connection claimed the same exemption, so the per-share download budget
    # could be spent without ever moving (audit 2026-07-30). The evidence here
    # is durable rather than the public path's short Redis mark: the desktop
    # client can pause a download and resume it the next day, so a
    # `download_log` row for THIS user and THIS file inside the credit window is
    # what buys the free continuation - and it grants nothing to a caller who
    # never downloaded the file.
    #
    # Corroborated by a Redis mark as well as the durable row, exactly as the
    # ZIP route below does. The DB lookup alone is an unlocked SELECT taken
    # before the counter is touched and the row is only committed at the end of
    # the request, so a client that opens several ranges at once - the desktop
    # client submits its whole first wave to a thread pool - had every segment
    # read "no prior download" and take the PAYING branch: N decrements, N
    # download_log rows, N audit rows for one logical download, which also
    # inflates anomaly.mass_download. On a share with download_limit=1 or 2 the
    # counter empties and the losing segments 410.
    #
    # The mark is written at the paying moment BELOW, before the commit, so a
    # concurrent sibling can see it immediately. The durable row remains the
    # restart-surviving fallback - the desktop client's overnight pause needs
    # it, and Redis being down must not start charging N.
    paid_key = f"user:{user.id}:file:{file.id}"
    is_continuation = is_partial_continuation(request) and (
        transfer_activity.was_download_paid(paid_key)
        or file_svc.has_recent_counted_download(
            db,
            file_id=file.id,
            user_id=user.id,
            within_hours=int(_sr.effective(db, _sr.K.DOWNLOAD_RESUME_CREDIT_HOURS)),
        )
    )

    # A size probe is not a download. The desktop client opens every transfer
    # with `Range: bytes=1-1` to learn the total and whether ranges are honoured
    # - which rode the old start-above-zero exemption, and which the corroborated
    # rule above took away with it. The result was a first download charged
    # twice, and a `download_limit=1` share that a browser could fetch and the
    # client could not. Charged on how much is TAKEN, not on where it starts;
    # see utils/http_range.is_metadata_probe.
    is_probe = is_metadata_probe(request.headers.get("range"), file.size_bytes)

    if not is_continuation and not is_probe:
        # A pending share only reaches here for an approver reviewing its
        # content. That must not consume the not-yet-live recipient budget -
        # but it IS a person who is not a recipient taking a full copy of
        # someone else's file, so it is recorded like any other transfer. It
        # used to be recorded nowhere at all: no download_log row, no audit
        # entry, nothing for the sender or an investigator to find (audit
        # 2026-07-30).
        is_review = share_svc.is_review_access(db, user=user, share=share)

        # v1.1.0: per-share download budget, live shares only. Atomic
        # decrement; if the counter is already at 0 we refuse with 410 before
        # logging/sending. NULL limit = unlimited, the helper's WHERE clause
        # skips the case.
        #
        # `not is_review` is load-bearing since v2.13.1: an approver reviewing
        # files appended to an ACTIVE share is not a recipient, and charging
        # them exhausts the recipients' budget before anyone receives anything.
        if (
            not is_review
            and share.state == ShareState.active
            and share.download_limit is not None
            and not share_svc.try_decrement_share_counter(db, share=share)
        ):
            raise AppError(
                410,
                "SHARE_DOWNLOAD_LIMIT_REACHED",
                "This share has reached its download limit.",
            )

        # Mark BEFORE the commit, so a concurrent sibling range sees the
        # payment immediately instead of racing the transaction. Only the
        # paying path marks, so a free continuation cannot renew its own
        # licence. Same discipline as the ZIP route below.
        transfer_activity.mark_download_paid(paid_key)

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
        metadata: dict[str, Any] = {"via": via.value, "share_id": file.share_id}
        if is_review:
            metadata["review"] = True
        record_audit_event(
            db,
            event_type=AuditEventType.file_downloaded,
            actor_user_id=user.id,
            target_type="file",
            target_id=file.id,
            metadata=metadata,
            request=request,
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


@router.get("/{share_id}/download-zip-url", response_model=SignedUrlResponse)
def get_share_zip_url(
    share_id: str,
    user: User = Depends(require_scope("files:download")),
    db: Session = Depends(get_db),
) -> dict:
    """Mint a short-lived signed URL for a bulk-ZIP of every downloadable file
    in a share. Mirrors `get_download_url` (single file): pre-flights the same
    access checks so the user gets a clean 4xx now rather than a broken-looking
    URL later, then issues a `?dt=` token bound to the SHARE id."""
    share = share_svc.get_share_or_404(db, share_id)
    share_svc.assert_share_file_access(db, user=user, share=share)
    from ..services import maintenance as maintenance_svc
    maintenance_svc.refuse_if_maintenance(db, kind="download")

    if not file_svc.downloadable_files(db, share.id):
        raise AppError(400, "NO_DOWNLOADABLE_FILES", "This share has no downloadable files.")

    # Refuse to mint when the budget is already spent (NULL = unlimited). The
    # atomic decrement at consume time still closes the mint→consume race.
    if share.download_limit is not None and (share.downloads_remaining or 0) <= 0:
        raise AppError(
            410, "SHARE_DOWNLOAD_LIMIT_REACHED", "This share has reached its download limit."
        )

    from ..services import settings_registry
    ttl = settings_registry.effective(db, settings_registry.K.DOWNLOAD_SIGNED_URL_TTL_SEC)
    token = download_token_svc.issue(share.id, user.id, ttl_sec=ttl)
    return {"url": f"/api/files/{share.id}/download-zip?dt={token}"}


@download_router.get("/{share_id}/download-zip")
def download_share_zip(
    share_id: str,
    request: Request,
    db: Session = Depends(get_db),
    dt: str | None = None,
    authorization: str | None = Header(default=None),
    range_header: str | None = Header(default=None, alias="Range"),
    if_range: str | None = Header(default=None, alias="If-Range"),
) -> Response:
    """Stream a single ZIP of all downloadable files in a share. On-the-fly,
    ZIP_STORED, O(1) memory (see services/zip_stream). Auth mirrors the
    single-file path: signed `?dt=` (bound to the share id) or bearer. One ZIP
    counts as ONE download against the share budget - a genuine resume doesn't
    re-decrement or re-log."""
    user = _resolve_download_user(request, db, share_id, dt, authorization)
    share = share_svc.get_share_or_404(db, share_id)

    share_svc.assert_share_file_access(db, user=user, share=share)

    from ..services import maintenance as maintenance_svc

    files = file_svc.downloadable_files(db, share.id)
    if not files:
        raise AppError(400, "NO_DOWNLOADABLE_FILES", "This share has no downloadable files.")

    # The archive is reproducible and seekable, so a Range is honoured for real
    # (see services/zip_stream). The timestamp comes from the share so the same
    # member list always produces the same bytes; the ETag covers the member
    # list, so a file quarantined mid-transfer makes `If-Range` miss and the
    # client restarts instead of splicing two archives.
    mtime = to_epoch(share.created_at)
    etag, total = zip_stream_svc.zip_identity(files, mtime=mtime)
    quoted_etag = f'"{etag}"'

    byte_range = None
    if if_range is None or if_range.strip() == quoted_etag:
        try:
            parsed = parse_single_range(range_header, total)
        except UnsatisfiableRangeError:
            # RFC 9110 15.5.17: a 416 carries the resource length so the client
            # can recover. A bare Response rather than the usual error envelope
            # because Content-Range is the entire point of the reply.
            return Response(
                status_code=416, headers={"Content-Range": f"bytes */{total}"}
            )
        byte_range = (parsed.start, parsed.end) if parsed else None

    # A continuation is free; a claim of one is not. Evidence must be that THIS
    # principal already PAID for THIS exact archive - keyed on the user and the
    # ETag, mirroring the public route.
    #
    # It used to be "a download_log row for this user and files[0] inside the
    # credit window", which is evidence of a FILE download, not of an archive
    # transfer in progress. A recipient who downloaded one member normally, and
    # so spent the share's last download, could then send `Range: bytes=1-` at
    # the ZIP and receive the complete archive - the 410 the plain request had
    # just returned - with the counter unmoved and no download_log or audit row
    # written. Adding a file to the share afterwards changed the ETag but not
    # that stale evidence, so the new member came out free too (audit #2).
    paid_key = f"user:{user.id}:zip:{share.id}:{etag}"
    resuming = byte_range is not None and byte_range[0] > 0
    corroborated = resuming and (
        transfer_activity.was_download_paid(paid_key)
        # ...or the durable record of the same payment. The Redis mark is the
        # fast path; this is what survives a Redis restart and an overnight
        # pause, which is exactly what the desktop client's resumable download
        # does. Archive-specific, unlike the per-file download_log row this
        # replaced.
        or share_svc.has_recent_archive_download(
            db,
            share_id=share.id,
            user_id=user.id,
            etag=etag,
            within_hours=int(_sr.effective(db, _sr.K.DOWNLOAD_RESUME_CREDIT_HOURS)),
        )
    )

    # Maintenance is refused HERE, after the archive identity is known, so a
    # CORROBORATED resume can finish.
    #
    # The gate used to sit above with a comment explaining that a ZIP "ignores
    # Range and always builds the WHOLE archive from scratch", which stopped
    # being true in v2.7.0 when the archive became seekable. Since then a
    # recipient 18 GB into a 20 GB archive whose connection dropped during a
    # postponed update got 503 and had to start over - which is the precise
    # opposite of what maintenance mode is for (audit #2). A resume with no
    # payment behind it is still a new transfer and is still refused.
    if not corroborated:
        maintenance_svc.refuse_if_maintenance(db, kind="download")

    if not corroborated:
        # A pending share only reaches here for an approver reviewing content.
        # That must not consume the not-yet-live recipient budget - but it IS a
        # person who is not a recipient taking a full copy of someone else's
        # files, so it is recorded like any other transfer. The single-file
        # route was fixed for this in v2.6.0 and the archive route, which hands
        # over EVERY file at once, was left recording nothing at all (audit #2).
        is_review = share_svc.is_review_access(db, user=user, share=share)

        if (
            not is_review
            and share.state == ShareState.active
            and share.download_limit is not None
            and not share_svc.try_decrement_share_counter(db, share=share)
        ):
            raise AppError(
                410, "SHARE_DOWNLOAD_LIMIT_REACHED", "This share has reached its download limit."
            )
        # Only the paying path marks, so a free continuation cannot renew it.
        transfer_activity.mark_download_paid(paid_key)

        via = (
            DownloadVia.api_token
            if getattr(request.state, "auth_via", "") == "api_token"
            else DownloadVia.auth
        )
        ip = request.client.host if request.client else None
        ua = ua_fingerprint_hash(request.headers.get("user-agent", ""))
        # One DownloadLog row per included file (file_id is NOT NULL) - keeps
        # per-file download attribution; the budget still moved only once above.
        for f in files:
            db.add(
                DownloadLog(
                    file_id=f.id,
                    share_id=share.id,
                    accessed_by_user_id=user.id,
                    ip=ip,
                    ua_fingerprint_hash=ua,
                    bytes_served=f.size_bytes,
                    via=via,
                )
            )
        # `etag` identifies WHICH archive was paid for. It is what makes a
        # resume corroborable after the Redis mark is gone - a restart (the
        # v2.5.0 host step performs one), a host reboot, or simply a pause
        # longer than the mark's window. Without it the only durable evidence
        # was a per-file download_log row, which is evidence of a FILE download
        # and is what made the bypass possible in the first place (audit #2
        # cross-check).
        metadata = {
            "via": via.value,
            "file_count": len(files),
            "archive": True,
            "etag": etag,
        }
        if is_review:
            metadata["review"] = True
        record_audit_event(
            db,
            event_type=AuditEventType.share_downloaded,
            actor_user_id=user.id,
            target_type="share",
            target_id=share.id,
            metadata=metadata,
            request=request,
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


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(
    file_id: str,
    request: Request,
    user: User = Depends(require_scope("files:delete")),
    db: Session = Depends(get_db),
) -> None:
    file = _get_file_or_404(db, file_id)
    if file.uploaded_by_id != user.id and user.role != UserRole.admin:
        raise AppError(403, "FORBIDDEN", "Only the uploader or an admin can delete this file.")
    if file.state == FileState.deleted:
        return  # idempotent
    if file.state == FileState.infected and user.role != UserRole.admin:
        raise AppError(
            403,
            "FILE_QUARANTINED_ADMIN_ONLY",
            "Antivirus flagged this file. Only an administrator can act on it "
            "via /admin/quarantine (release back, purge, or download).",
        )
    share_id = file.share_id
    # Deferred purge: the bytes go after the commit, so a commit failure cannot
    # leave a `clean` row pointing at nothing (audit #2).
    purge = file_svc.hard_delete(
        db, file=file, reason="user_request", request=request, purge=False
    )
    # If this was the last non-deleted file in an active share, auto-revoke it
    # (shared helper - same behavior as the admin delete path).
    file_svc.revoke_share_if_empty(
        db,
        share_id=share_id,
        just_deleted_file_id=file.id,
        actor_user_id=user.id,
        request=request,
    )
    db.commit()
    file_svc.purge_locators(db, [purge], reason="file_deleted")
