"""Quarantine notify-admins toggle.

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
from ....schemas.quarantine import (
    QuarantineSettingsResponse,
    UpdateQuarantineSettingsRequest,
)
from ....services import settings as settings_svc
from ....services.audit import record_audit_event

router = APIRouter()


# ---- Quarantine notify-admins toggle --------------------------------------


@router.get("/settings/quarantine", response_model=QuarantineSettingsResponse)
def get_quarantine_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> QuarantineSettingsResponse:
    return QuarantineSettingsResponse(
        notify_admins=settings_svc.get_bool(
            db, settings_svc.Keys.QUARANTINE_NOTIFY_ADMINS, default=False
        )
    )


@router.put("/settings/quarantine", response_model=QuarantineSettingsResponse)
def update_quarantine_settings(
    payload: UpdateQuarantineSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> QuarantineSettingsResponse:
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.QUARANTINE_NOTIFY_ADMINS,
        value="true" if payload.notify_admins else "false",
        actor=admin,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.quarantine_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="quarantine",
        metadata={"notify_admins": payload.notify_admins},
        request=request,
    )
    db.commit()
    return QuarantineSettingsResponse(notify_admins=payload.notify_admins)
