"""Per-share defaults (recipient notification).

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
from ....schemas.share_defaults_settings import (
    ShareDefaultsResponse,
    UpdateShareDefaultsRequest,
)
from ....services import settings as settings_svc
from ....services.audit import record_audit_event

router = APIRouter()


# ---- Share defaults --------------------------------------------------------


@router.get("/settings/share-defaults", response_model=ShareDefaultsResponse)
def get_share_defaults_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> ShareDefaultsResponse:
    enabled = settings_svc.get_bool(
        db, settings_svc.Keys.SHARE_NOTIFY_RECIPIENTS_DEFAULT, default=True
    )
    return ShareDefaultsResponse(notify_recipients_default=enabled)


@router.put("/settings/share-defaults", response_model=ShareDefaultsResponse)
def update_share_defaults_settings(
    payload: UpdateShareDefaultsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> ShareDefaultsResponse:
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.SHARE_NOTIFY_RECIPIENTS_DEFAULT,
        value="true" if payload.notify_recipients_default else "false",
        actor=admin,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.share_defaults_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="share_defaults",
        metadata={"notify_recipients_default": payload.notify_recipients_default},
        request=request,
    )
    db.commit()
    return ShareDefaultsResponse(
        notify_recipients_default=payload.notify_recipients_default
    )
