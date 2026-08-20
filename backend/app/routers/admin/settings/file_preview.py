"""In-browser preview toggle.

Split out of the 1,581-line `routers/admin/settings.py` (v2.13.x). Pure
move: no route path, body, or behaviour changed. The clusters had no
cross-references to each other - every private helper was already used
only inside its own section.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ....dependencies import get_current_admin, get_db
from ....models.audit_log import AuditEventType
from ....models.user import User
from ....schemas.file_preview_settings import (
    FilePreviewSettingsResponse,
    UpdateFilePreviewSettingsRequest,
)
from ....services import settings as settings_svc
from ....services.audit import record_audit_event

router = APIRouter()


# ---- File preview (in-browser) ---------------------------------------------


@router.get("/settings/file-preview", response_model=FilePreviewSettingsResponse)
def get_file_preview_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> FilePreviewSettingsResponse:
    return FilePreviewSettingsResponse(
        enabled=settings_svc.get_bool(
            db, settings_svc.Keys.FILE_PREVIEW_ENABLED, default=True
        )
    )


@router.put("/settings/file-preview", response_model=FilePreviewSettingsResponse)
def update_file_preview_settings(
    payload: UpdateFilePreviewSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> FilePreviewSettingsResponse:
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.FILE_PREVIEW_ENABLED,
        value="true" if payload.enabled else "false",
        actor=admin,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.file_preview_toggled,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="file_preview",
        metadata={"enabled": payload.enabled},
        request=request,
    )
    db.commit()
    return FilePreviewSettingsResponse(enabled=payload.enabled)
