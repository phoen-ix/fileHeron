"""Server-error email alerts and 4xx capture.

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
from ....schemas.error_alert_settings import (
    ErrorAlertSettingsResponse,
    UpdateErrorAlertSettingsRequest,
)
from ....services import error_alert
from ....services.audit import record_audit_event

router = APIRouter()


# ---- Error alerts (email admins on server errors) -------------------------


@router.get("/settings/error-alerts", response_model=ErrorAlertSettingsResponse)
def get_error_alert_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> ErrorAlertSettingsResponse:
    return ErrorAlertSettingsResponse(**error_alert.get_settings(db))


@router.put("/settings/error-alerts", response_model=ErrorAlertSettingsResponse)
def update_error_alert_settings(
    payload: UpdateErrorAlertSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> ErrorAlertSettingsResponse:
    result = error_alert.update_settings(
        db,
        enabled=payload.enabled,
        source_http_5xx=payload.source_http_5xx,
        source_http_4xx=payload.source_http_4xx,
        recipients_mode=payload.recipients_mode,
        custom_recipients=payload.custom_recipients,
        cooldown_minutes=payload.cooldown_minutes,
        max_per_hour=payload.max_per_hour,
        log_enabled=payload.log_enabled,
        capture_4xx=payload.capture_4xx,
        http_4xx_codes=payload.http_4xx_codes,
        retention_days=payload.retention_days,
        actor=admin,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.error_alert_settings_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="error_alerts",
        # Record counts/keys only - never the recipient addresses themselves.
        metadata={
            "enabled": payload.enabled,
            "source_http_5xx": payload.source_http_5xx,
            "source_http_4xx": payload.source_http_4xx,
            "recipients_mode": payload.recipients_mode,
            "recipient_count": len(payload.custom_recipients),
            "cooldown_minutes": payload.cooldown_minutes,
            "max_per_hour": payload.max_per_hour,
            "log_enabled": payload.log_enabled,
            "capture_4xx": payload.capture_4xx,
            "http_4xx_code_count": len(payload.http_4xx_codes),
            "retention_days": payload.retention_days,
        },
        request=request,
    )
    db.commit()
    return ErrorAlertSettingsResponse(**result)
