"""/api/admin/backup - configuration backup export + restore (v1.33.0).

Export streams a versioned ``*.fhbackup.json``; import previews then replaces the
in-scope configuration and invalidates ALL active shares. Admin-only.
"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.audit_log import AuditEventType
from ...models.user import User
from ...schemas.backup import BackupExportRequest, BackupImportSummaryResponse
from ...services import config_backup as cb
from ...services.audit import record_audit_event
from ...utils.timeutil import utc_now

router = APIRouter()

# Hard cap on an uploaded backup, enforced before json.loads to guard OOM / a
# JSON bomb. The logo byte cap bounds the largest legitimate field.
_MAX_IMPORT_BYTES = 50 * 1024 * 1024


@router.post("/backup/export")
def export_backup(
    payload: BackupExportRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> StreamingResponse:
    data = cb.build_backup(
        db,
        categories=payload.categories,
        secret_mode=payload.secret_mode,
        passphrase=payload.passphrase,
        include_env=payload.include_env,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.config_backup_exported,
        actor_user_id=admin.id,
        target_type="config_backup",
        target_id=None,
        metadata={
            "categories": payload.categories,
            "secret_mode": payload.secret_mode,
            "include_env": payload.include_env,
        },
        request=request,
    )
    db.commit()
    stamp = utc_now().strftime("%Y%m%d-%H%M%S")
    filename = f"fileheron-config-{stamp}.fhbackup.json"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _read_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    received = 0
    while True:
        chunk = await file.read(256 * 1024)
        if not chunk:
            break
        received += len(chunk)
        if received > _MAX_IMPORT_BYTES:
            raise AppError(
                413, "BACKUP_TOO_LARGE",
                f"Backup exceeds the {_MAX_IMPORT_BYTES // (1024 * 1024)} MB import limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _to_response(summary: cb.ImportSummary) -> BackupImportSummaryResponse:
    return BackupImportSummaryResponse(
        dry_run=summary.dry_run,
        secret_mode=summary.secret_mode,
        categories=summary.categories,
        shares_to_invalidate=summary.shares_to_invalidate,
        files_deleted=summary.files_deleted,
        counts=summary.counts,
        purged_users=summary.purged_users,
        purged_groups=summary.purged_groups,
        sessions_revoked=summary.sessions_revoked,
        env_snapshot_present=summary.env_snapshot_present,
        env_dotenv=summary.env_dotenv,
        version_warning=summary.version_warning,
        warnings=summary.warnings,
    )


@router.post("/backup/import/preview", response_model=BackupImportSummaryResponse)
async def preview_import(
    file: UploadFile = File(...),
    passphrase: str | None = Form(None),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> BackupImportSummaryResponse:
    raw = await _read_upload(file)
    parsed = cb.parse_backup(raw, passphrase=passphrase)
    return _to_response(cb.preview_backup(db, parsed))


@router.post("/backup/import", response_model=BackupImportSummaryResponse)
async def import_backup(
    request: Request,
    file: UploadFile = File(...),
    passphrase: str | None = Form(None),
    confirm: bool = Form(False),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> BackupImportSummaryResponse:
    if not confirm:
        raise AppError(
            400, "BACKUP_CONFIRM_REQUIRED",
            "Import replaces configuration and invalidates all shares; confirm=true is required.",
        )
    raw = await _read_upload(file)
    parsed = cb.parse_backup(raw, passphrase=passphrase)
    summary = cb.apply_backup(db, parsed=parsed, actor=admin, request=request)
    return _to_response(summary)
