"""/api/admin/files/{id}/quarantine - admin actions on infected files.

Also surfaces v1.1.6 read-only AV-engine info + manual reload at
/api/admin/quarantine/{av-status,av-reload}.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.audit_log import AuditEventType, AuditLog
from ...models.file import File, FileState
from ...models.user import User
from ...schemas.quarantine import (
    AvReloadResponse,
    AvStatusResponse,
    QuarantineActionRequest,
)
from ...services import av_scan as av_scan_svc
from ...services import quarantine_admin as quarantine_admin_svc
from ...services.audit import record_audit_event

router = APIRouter()


def _get_infected_file_or_404(db: Session, file_id: str) -> File:
    file = db.query(File).filter(File.id == file_id).one_or_none()
    if file is None or file.state != FileState.infected:
        raise AppError(
            404,
            "QUARANTINED_FILE_NOT_FOUND",
            "No quarantined file with that id.",
        )
    return file


@router.get("/files/{file_id}/quarantine/download")
def admin_quarantine_download(
    file_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> Response:
    """Stream the quarantined bytes for forensic inspection. The
    ``.quarantined`` suffix is a belt-and-braces hint not to double-click
    the resulting download - the admin's own AV should also flag it."""
    from ...services import settings_registry
    from ...services.storage_backend import get_storage_backend, serve_response

    file = _get_infected_file_or_404(db, file_id)
    backend = get_storage_backend()
    if not file.storage_path:
        raise AppError(404, "QUARANTINE_BYTES_MISSING", "Bytes already purged for this file.")
    if not backend.exists(file.storage_path):
        raise AppError(404, "QUARANTINE_BYTES_MISSING", "Quarantine bytes are missing.")
    ttl = settings_registry.effective(db, settings_registry.K.DOWNLOAD_SIGNED_URL_TTL_SEC)
    return serve_response(
        backend,
        locator=file.storage_path,
        filename=f"{file.original_filename}.quarantined",
        mime_type="application/octet-stream",
        ttl_sec=ttl,
    )


@router.post(
    "/files/{file_id}/quarantine/release",
    status_code=status.HTTP_204_NO_CONTENT,
)
def admin_quarantine_release(
    file_id: str,
    payload: QuarantineActionRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> Response:
    file = _get_infected_file_or_404(db, file_id)
    quarantine_admin_svc.release(
        db, admin=admin, file=file, reason=payload.reason, request=request
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/files/{file_id}/quarantine", status_code=status.HTTP_204_NO_CONTENT
)
def admin_quarantine_purge(
    file_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> Response:
    """Purge takes no body - admin already saw the file in the
    quarantine list and clicked through a confirm dialog. The
    file_quarantine_purged audit row records the actor + file
    metadata; that's enough provenance."""
    file = _get_infected_file_or_404(db, file_id)
    quarantine_admin_svc.purge(db, admin=admin, file=file, request=request)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# v1.1.6 - AV engine status + manual signature reload
# ---------------------------------------------------------------------------


@router.get("/quarantine/av-status", response_model=AvStatusResponse)
def av_status(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AvStatusResponse:
    info = av_scan_svc.get_version()
    last_reload_at = (
        db.query(AuditLog.created_at)
        .filter(AuditLog.event_type == AuditEventType.av_reload_triggered)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(1)
        .scalar()
    )
    return AvStatusResponse(**info, last_reload_at=last_reload_at)


@router.post("/quarantine/av-reload", response_model=AvReloadResponse)
def av_reload(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> AvReloadResponse:
    """Tell clamd to re-read its signature DB from disk. Updates
    fetched by freshclam in the background land in the running engine
    without a container restart. Audits regardless of outcome so the
    operator trail captures "they tried, it didn't work" as much as
    "they tried, it succeeded."
    """
    try:
        result = av_scan_svc.reload_signatures()
    except av_scan_svc.AVUnavailableError as e:
        record_audit_event(
            db,
            event_type=AuditEventType.av_reload_triggered,
            actor_user_id=admin.id,
            target_type="av_engine",
            target_id="clamd",
            metadata={"ok": False, "av_skip": False, "error": str(e)},
            request=request,
        )
        db.commit()
        raise AppError(503, "AV_UNAVAILABLE", f"ClamAV unreachable: {e}") from e

    record_audit_event(
        db,
        event_type=AuditEventType.av_reload_triggered,
        actor_user_id=admin.id,
        target_type="av_engine",
        target_id="clamd",
        metadata={"ok": result["ok"], "av_skip": result["av_skip"], "raw": result["raw"]},
        request=request,
    )
    db.commit()
    if not result["ok"]:
        # AV_SKIP dev mode, or clamd returned a non-RELOAD reply.
        raise AppError(
            503,
            "AV_UNAVAILABLE",
            "ClamAV engine did not accept the reload signal.",
        )
    return AvReloadResponse(**result)
