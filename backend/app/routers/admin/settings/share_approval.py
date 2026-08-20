"""Four-eyes share-approval policy (v1.24.0).

Split out of the 1,581-line `routers/admin/settings.py` (v2.13.x). Pure
move: no route path, body, or behaviour changed. The clusters had no
cross-references to each other - every private helper was already used
only inside its own section.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ....dependencies import get_current_admin, get_db
from ....middleware.errors import AppError
from ....models.audit_log import AuditEventType
from ....models.group import Group
from ....models.user import User
from ....schemas.share_approval_settings import (
    ApproverGroupRef,
    ApproverUserRef,
    ShareApprovalSettingsResponse,
    UpdateShareApprovalSettingsRequest,
)
from ....services import settings as settings_svc
from ....services import share_approval as share_approval_svc
from ....services.audit import record_audit_event

router = APIRouter()


# ---- Share-approval policy (v1.24.0) ---------------------------------------


def _share_approval_response(db: Session) -> ShareApprovalSettingsResponse:
    mode, user_ids, group_ids = share_approval_svc.resolve_approver_policy(db)
    users = db.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
    groups = db.query(Group).filter(Group.id.in_(group_ids)).all() if group_ids else []
    return ShareApprovalSettingsResponse(
        enabled=share_approval_svc.is_enabled(db),
        approver_mode=mode,  # type: ignore[arg-type]
        approver_user_ids=user_ids,
        approver_group_ids=group_ids,
        approver_users=[
            ApproverUserRef(
                id=u.id, display_name=u.display_name, email=u.email, role=u.role.value
            )
            for u in users
        ],
        approver_groups=[ApproverGroupRef(id=g.id, name=g.name) for g in groups],
        scope=share_approval_svc.effective_scope(db),  # type: ignore[arg-type]
        exempt_approvers=share_approval_svc.exempt_approvers(db),
        allow_content_review=share_approval_svc.allow_content_review(db),
        is_inert=share_approval_svc.is_inert(db),
    )


@router.get("/settings/share-approval", response_model=ShareApprovalSettingsResponse)
def get_share_approval_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> ShareApprovalSettingsResponse:
    return _share_approval_response(db)


@router.put("/settings/share-approval", response_model=ShareApprovalSettingsResponse)
def update_share_approval_settings(
    payload: UpdateShareApprovalSettingsRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> ShareApprovalSettingsResponse:
    if payload.approver_user_ids:
        found = {
            row[0]
            for row in db.query(User.id)
            .filter(User.id.in_(payload.approver_user_ids))
            .all()
        }
        missing = [i for i in payload.approver_user_ids if i not in found]
        if missing:
            raise AppError(
                400,
                "USER_NOT_FOUND",
                "One or more selected users do not exist.",
                details={"missing_user_ids": missing},
            )
    if payload.approver_group_ids:
        found = {
            row[0]
            for row in db.query(Group.id)
            .filter(Group.id.in_(payload.approver_group_ids))
            .all()
        }
        missing = [i for i in payload.approver_group_ids if i not in found]
        if missing:
            raise AppError(
                400,
                "GROUP_NOT_FOUND",
                "One or more selected groups do not exist.",
                details={"missing_group_ids": missing},
            )

    # Refuse a combination that can never queue anything. "Every employee may
    # approve" plus "an approver's own shares are exempt" cancel each other out:
    # staff create outbound shares, every employee is an approver, so every
    # outbound share is exempted at birth and only inbound (client) shares are
    # left - which the outbound scopes exclude. The result is a four-eyes
    # control that is on, looks configured, and stops nothing. Worse than off,
    # because it manufactures assurance (audit 2026-07-30).
    if payload.enabled and share_approval_svc.policy_is_inert(
        payload.approver_mode, payload.scope, payload.exempt_approvers
    ):
        raise AppError(
            400,
            "APPROVAL_POLICY_INERT",
            "This combination means no share can ever require approval: every "
            "employee is an approver, and approvers' own shares are exempt. "
            "Set the approver mode to admins only, turn off the approver "
            "exemption, or widen the scope to all shares.",
            details={
                "approver_mode": payload.approver_mode,
                "scope": payload.scope,
                "exempt_approvers": payload.exempt_approvers,
            },
        )

    keys = settings_svc.Keys
    settings_svc.set_value(
        db, key=keys.SHARE_APPROVAL_ENABLED, value="true" if payload.enabled else "false", actor=admin
    )
    settings_svc.set_value(db, key=keys.SHARE_APPROVAL_APPROVER_MODE, value=payload.approver_mode, actor=admin)
    settings_svc.set_value(
        db,
        key=keys.SHARE_APPROVAL_APPROVER_USERS,
        value=json.dumps(payload.approver_user_ids) if payload.approver_user_ids else None,
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=keys.SHARE_APPROVAL_APPROVER_GROUPS,
        value=json.dumps(payload.approver_group_ids) if payload.approver_group_ids else None,
        actor=admin,
    )
    settings_svc.set_value(db, key=keys.SHARE_APPROVAL_SCOPE, value=payload.scope, actor=admin)
    settings_svc.set_value(
        db,
        key=keys.SHARE_APPROVAL_EXEMPT_APPROVERS,
        value="true" if payload.exempt_approvers else "false",
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=keys.SHARE_APPROVAL_ALLOW_CONTENT_REVIEW,
        value="true" if payload.allow_content_review else "false",
        actor=admin,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.share_approval_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="share_approval",
        metadata={
            "enabled": payload.enabled,
            "mode": payload.approver_mode,
            "scope": payload.scope,
            "user_count": len(payload.approver_user_ids),
            "group_count": len(payload.approver_group_ids),
            "exempt_approvers": payload.exempt_approvers,
            "allow_content_review": payload.allow_content_review,
        },
        request=request,
    )
    db.commit()
    return _share_approval_response(db)
