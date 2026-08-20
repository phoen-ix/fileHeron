"""Email-change verification + OIDC-reset policy.

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
from ....schemas.admin import (
    EmailChangePolicyResponse,
    UpdateEmailChangePolicyRequest,
)
from ....services import email_change_policy
from ....services import settings as settings_svc
from ....services.audit import record_audit_event

router = APIRouter()


# ---- Email-change policy ---------------------------------------------------


def _email_change_policy_response(db: Session) -> EmailChangePolicyResponse:
    return EmailChangePolicyResponse(
        verification_mode=email_change_policy.effective_verification_mode(db),
        self_service=email_change_policy.self_service_enabled(db),
        oidc_mode=email_change_policy.effective_oidc_mode(db),
    )


@router.get("/settings/email-change", response_model=EmailChangePolicyResponse)
def get_email_change_policy(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> EmailChangePolicyResponse:
    return _email_change_policy_response(db)


@router.put("/settings/email-change", response_model=EmailChangePolicyResponse)
def update_email_change_policy(
    payload: UpdateEmailChangePolicyRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> EmailChangePolicyResponse:
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.EMAIL_CHANGE_VERIFICATION_MODE,
        value=payload.verification_mode,
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.EMAIL_CHANGE_SELF_SERVICE,
        value="true" if payload.self_service else "false",
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.EMAIL_CHANGE_OIDC_MODE,
        value=payload.oidc_mode,
        actor=admin,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.email_change_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="email_change",
        metadata={
            "verification_mode": payload.verification_mode,
            "self_service": payload.self_service,
            "oidc_mode": payload.oidc_mode,
        },
        request=request,
    )
    db.commit()
    return _email_change_policy_response(db)
