"""/api/admin/files/{id}/quarantine — admin actions on infected files."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.file import File, FileState
from ...models.user import User
from ...schemas.quarantine import QuarantineActionRequest
from ...services import quarantine_admin as quarantine_admin_svc

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
) -> FileResponse:
    """Stream the quarantined bytes for forensic inspection. The
    ``.quarantined`` suffix is a belt-and-braces hint not to double-click
    the resulting download — the admin's own AV should also flag it."""
    file = _get_infected_file_or_404(db, file_id)
    if not file.storage_path:
        raise AppError(
            404,
            "QUARANTINE_BYTES_MISSING",
            "Bytes already purged for this file.",
        )
    if not Path(file.storage_path).is_file():
        raise AppError(
            404,
            "QUARANTINE_BYTES_MISSING",
            "Quarantine file is missing on disk.",
        )
    suggested = f"{file.original_filename}.quarantined"
    return FileResponse(
        file.storage_path,
        media_type="application/octet-stream",
        filename=suggested,
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
    """Purge takes no body — admin already saw the file in the
    quarantine list and clicked through a confirm dialog. The
    file_quarantine_purged audit row records the actor + file
    metadata; that's enough provenance."""
    file = _get_infected_file_or_404(db, file_id)
    quarantine_admin_svc.purge(db, admin=admin, file=file, request=request)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
