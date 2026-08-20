"""Maintenance mode (pause new transfers).

Split out of the 1,581-line `routers/admin/settings.py` (v2.13.x). Pure
move: no route path, body, or behaviour changed. The clusters had no
cross-references to each other - every private helper was already used
only inside its own section.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ....dependencies import get_current_admin, get_db
from ....middleware.errors import AppError
from ....models.user import User
from ....schemas.maintenance import (
    MaintenanceSettingsResponse,
    UpdateMaintenanceSettingsRequest,
)

router = APIRouter()


# ---- Maintenance mode ------------------------------------------------------


def _maintenance_response(db: Session) -> MaintenanceSettingsResponse:
    from ....services import maintenance as maintenance_svc
    from ....services import transfer_activity as ta

    snap = ta.snapshot(db)
    return MaintenanceSettingsResponse(
        enabled=maintenance_svc.is_enabled(db),
        message=maintenance_svc.get_message(db),
        active_uploads=snap["active_uploads"],
        active_downloads=snap["active_downloads"],
    )


@router.get("/settings/maintenance", response_model=MaintenanceSettingsResponse)
def get_maintenance_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> MaintenanceSettingsResponse:
    return _maintenance_response(db)


@router.put("/settings/maintenance", response_model=MaintenanceSettingsResponse)
def update_maintenance_settings(
    payload: UpdateMaintenanceSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> MaintenanceSettingsResponse:
    from ....services import maintenance as maintenance_svc

    # Refuse to switch maintenance OFF from here while an update is postponed.
    # This page and /admin/system share one flag: turning it off here left
    # `maintenance.pending_update` armed, so the minute drain worker still saw a
    # drained stack and restarted it - an unannounced restart during what the
    # admin believed was normal operation, because they thought they had
    # cancelled something (audit 2026-07-30). Cancelling is a different action
    # with a different audit event, so point them at it rather than guessing.
    if not payload.enabled and maintenance_svc.get_pending_update(db) is not None:
        raise AppError(
            409,
            "UPDATE_PENDING",
            "An update is postponed and waiting for transfers to drain. Cancel "
            "it on the System page before leaving maintenance mode.",
        )
    maintenance_svc.set_enabled(
        db, payload.enabled, actor=admin, message=payload.message, request=request
    )
    db.commit()
    return _maintenance_response(db)
