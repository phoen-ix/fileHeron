"""2FA enforcement by role and group.

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
from ....models.audit_log import AuditEventType
from ....models.group import Group
from ....models.user import User
from ....schemas.twofa_policy import (
    RequiredGroupRef,
    TwofaPolicyResponse,
    UpdateTwofaPolicyRequest,
)
from ....services import twofa_policy as twofa_policy_svc
from ....services.audit import record_audit_event

router = APIRouter()


# ---- 2FA enforcement -------------------------------------------------------


def _twofa_policy_response(db: Session) -> TwofaPolicyResponse:
    roles, group_ids, is_kv_overridden = twofa_policy_svc._resolve_policy(db)
    groups = (
        db.query(Group).filter(Group.id.in_(group_ids)).all() if group_ids else []
    )
    by_id = {g.id: g for g in groups}
    return TwofaPolicyResponse(
        required_roles=sorted(roles),
        required_group_ids=group_ids,
        required_groups=[
            RequiredGroupRef(
                id=g.id,
                name=g.name,
                is_company_inbox=getattr(g, "is_company_inbox", False),
            )
            for gid in group_ids
            if (g := by_id.get(gid)) is not None
        ],
        is_kv_overridden=is_kv_overridden,
    )


@router.get("/settings/twofa", response_model=TwofaPolicyResponse)
def get_twofa_policy(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> TwofaPolicyResponse:
    return _twofa_policy_response(db)


@router.put("/settings/twofa", response_model=TwofaPolicyResponse)
def update_twofa_policy(
    payload: UpdateTwofaPolicyRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> TwofaPolicyResponse:
    bad_roles = [
        r for r in payload.required_roles if r not in twofa_policy_svc.ALLOWED_ROLES
    ]
    if bad_roles:
        raise AppError(
            400,
            "INVALID_ROLE",
            "One or more role names are not recognised.",
            details={"invalid_roles": bad_roles},
        )

    if payload.required_group_ids:
        found_group_ids = {
            row[0]
            for row in db.query(Group.id)
            .filter(Group.id.in_(payload.required_group_ids))
            .all()
        }
        missing = [
            gid for gid in payload.required_group_ids if gid not in found_group_ids
        ]
        if missing:
            raise AppError(
                400,
                "GROUP_NOT_FOUND",
                "One or more selected groups do not exist.",
                details={"missing_group_ids": missing},
            )

    twofa_policy_svc.write_policy(
        db,
        actor=admin,
        required_roles=payload.required_roles,
        required_group_ids=payload.required_group_ids,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.twofa_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="twofa_policy",
        metadata={
            "role_count": len(set(payload.required_roles)),
            "group_count": len(set(payload.required_group_ids)),
        },
        request=request,
    )
    db.commit()
    return _twofa_policy_response(db)
