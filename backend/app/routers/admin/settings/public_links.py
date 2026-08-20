"""Public-link creation policy (mode + allowlists).

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
from ....schemas.public_link import (
    PublicLinkAllowedGroup,
    PublicLinkAllowedUser,
    PublicLinkPolicyResponse,
    UpdatePublicLinkPolicyRequest,
)
from ....services import public_link as public_link_svc
from ....services import settings as settings_svc
from ....services.audit import record_audit_event

router = APIRouter()


# ---- Public link policy ----------------------------------------------------


@router.get(
    "/settings/public-links/policy", response_model=PublicLinkPolicyResponse
)
def get_public_link_policy(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> PublicLinkPolicyResponse:
    mode, user_ids, group_ids = public_link_svc._resolve_policy(db)
    users = (
        db.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
    )
    groups = (
        db.query(Group).filter(Group.id.in_(group_ids)).all() if group_ids else []
    )
    return PublicLinkPolicyResponse(
        mode=mode,  # type: ignore[arg-type]
        allowed_user_ids=user_ids,
        allowed_group_ids=group_ids,
        allowed_users=[
            PublicLinkAllowedUser(
                id=u.id,
                display_name=u.display_name,
                email=u.email,
                role=u.role.value,
            )
            for u in users
        ],
        allowed_groups=[
            PublicLinkAllowedGroup(id=g.id, name=g.name) for g in groups
        ],
    )


@router.put(
    "/settings/public-links/policy", response_model=PublicLinkPolicyResponse
)
def update_public_link_policy(
    payload: UpdatePublicLinkPolicyRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> PublicLinkPolicyResponse:
    if payload.allowed_user_ids:
        found_user_ids = {
            row[0]
            for row in db.query(User.id)
            .filter(User.id.in_(payload.allowed_user_ids))
            .all()
        }
        missing = [
            uid for uid in payload.allowed_user_ids if uid not in found_user_ids
        ]
        if missing:
            raise AppError(
                400,
                "USER_NOT_FOUND",
                "One or more selected users do not exist.",
                details={"missing_user_ids": missing},
            )
    if payload.allowed_group_ids:
        found_group_ids = {
            row[0]
            for row in db.query(Group.id)
            .filter(Group.id.in_(payload.allowed_group_ids))
            .all()
        }
        missing = [
            gid for gid in payload.allowed_group_ids if gid not in found_group_ids
        ]
        if missing:
            raise AppError(
                400,
                "GROUP_NOT_FOUND",
                "One or more selected groups do not exist.",
                details={"missing_group_ids": missing},
            )

    settings_svc.set_value(
        db,
        key=settings_svc.Keys.PUBLIC_LINK_POLICY_MODE,
        value=payload.mode,
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.PUBLIC_LINK_ALLOWED_USERS,
        value=json.dumps(payload.allowed_user_ids) if payload.allowed_user_ids else None,
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.PUBLIC_LINK_ALLOWED_GROUPS,
        value=json.dumps(payload.allowed_group_ids) if payload.allowed_group_ids else None,
        actor=admin,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.public_link_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="public_link_policy",
        metadata={
            "mode": payload.mode,
            "user_count": len(payload.allowed_user_ids),
            "group_count": len(payload.allowed_group_ids),
        },
        request=request,
    )
    db.commit()
    return get_public_link_policy(db=db, _admin=admin)
