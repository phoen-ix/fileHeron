"""Secrets policy (v2.24.0): the instance switch, the two policy gates and the
wrong-passphrase mode.

The ceilings and throttle numbers are registry tunables, written by the one
registry writer (`/settings/advanced`) and rendered on the same admin page.
These keys are not, because they are allowlists like the public-link policy.
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
from ....schemas.secret import (
    SecretAllowedGroup,
    SecretAllowedUser,
    SecretPolicyResponse,
    SecretPolicySide,
    UpdateSecretPolicyRequest,
    UpdateSecretPolicySide,
)
from ....services import secret as secret_svc
from ....services import settings as settings_svc
from ....services.audit import record_audit_event

router = APIRouter()
K = settings_svc.Keys


def _side(db: Session, policy: tuple[str, list[int], list[int]]) -> SecretPolicySide:
    mode, user_ids, group_ids = policy
    users = db.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
    groups = db.query(Group).filter(Group.id.in_(group_ids)).all() if group_ids else []
    return SecretPolicySide(
        mode=mode,  # type: ignore[arg-type]
        allowed_user_ids=user_ids,
        allowed_group_ids=group_ids,
        allowed_users=[
            SecretAllowedUser(id=u.id, display_name=u.display_name, email=u.email, role=u.role.value)
            for u in users
        ],
        allowed_groups=[SecretAllowedGroup(id=g.id, name=g.name) for g in groups],
    )


@router.get("/settings/secrets", response_model=SecretPolicyResponse)
def get_secret_policy(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> SecretPolicyResponse:
    return SecretPolicyResponse(
        enabled=secret_svc.is_enabled(db),
        send=_side(db, secret_svc.resolve_send_policy(db)),
        external=_side(db, secret_svc.resolve_external_policy(db)),
        passphrase_failure_mode=secret_svc.passphrase_failure_mode(db),  # type: ignore[arg-type]
    )


def _check_ids(db: Session, side: UpdateSecretPolicySide) -> None:
    if side.allowed_user_ids:
        found = {
            uid for (uid,) in db.query(User.id).filter(User.id.in_(side.allowed_user_ids))
        }
        missing = [uid for uid in side.allowed_user_ids if uid not in found]
        if missing:
            raise AppError(
                400,
                "USER_NOT_FOUND",
                "One or more selected users do not exist.",
                details={"missing_user_ids": missing},
            )
    if side.allowed_group_ids:
        found = {
            gid for (gid,) in db.query(Group.id).filter(Group.id.in_(side.allowed_group_ids))
        }
        missing = [gid for gid in side.allowed_group_ids if gid not in found]
        if missing:
            raise AppError(
                400,
                "GROUP_NOT_FOUND",
                "One or more selected groups do not exist.",
                details={"missing_group_ids": missing},
            )


def _write_side(
    db: Session,
    side: UpdateSecretPolicySide,
    *,
    mode_key: str,
    users_key: str,
    groups_key: str,
    admin: User,
) -> None:
    ids_users = list(dict.fromkeys(side.allowed_user_ids))
    ids_groups = list(dict.fromkeys(side.allowed_group_ids))
    settings_svc.set_value(db, key=mode_key, value=side.mode, actor=admin)
    settings_svc.set_value(
        db, key=users_key, value=json.dumps(ids_users) if ids_users else None, actor=admin
    )
    settings_svc.set_value(
        db, key=groups_key, value=json.dumps(ids_groups) if ids_groups else None, actor=admin
    )


@router.put("/settings/secrets", response_model=SecretPolicyResponse)
def update_secret_policy(
    payload: UpdateSecretPolicyRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> SecretPolicyResponse:
    for side in (payload.send, payload.external):
        if side is not None:
            _check_ids(db, side)
    written: list[str] = []
    metadata: dict[str, object] = {}
    if payload.enabled is not None:
        settings_svc.set_value(
            db, key=K.SECRETS_ENABLED, value="true" if payload.enabled else "false", actor=admin
        )
        written.append(K.SECRETS_ENABLED)
        metadata["enabled"] = payload.enabled
    if payload.send is not None:
        _write_side(
            db,
            payload.send,
            mode_key=K.SECRETS_SEND_POLICY_MODE,
            users_key=K.SECRETS_SEND_ALLOWED_USERS,
            groups_key=K.SECRETS_SEND_ALLOWED_GROUPS,
            admin=admin,
        )
        written.append(K.SECRETS_SEND_POLICY_MODE)
        metadata["send"] = {
            "mode": payload.send.mode,
            "user_count": len(payload.send.allowed_user_ids),
            "group_count": len(payload.send.allowed_group_ids),
        }
    if payload.external is not None:
        _write_side(
            db,
            payload.external,
            mode_key=K.SECRETS_EXTERNAL_POLICY_MODE,
            users_key=K.SECRETS_EXTERNAL_ALLOWED_USERS,
            groups_key=K.SECRETS_EXTERNAL_ALLOWED_GROUPS,
            admin=admin,
        )
        written.append(K.SECRETS_EXTERNAL_POLICY_MODE)
        metadata["external"] = {
            "mode": payload.external.mode,
            "user_count": len(payload.external.allowed_user_ids),
            "group_count": len(payload.external.allowed_group_ids),
        }
    if payload.passphrase_failure_mode is not None:
        settings_svc.set_value(
            db,
            key=K.SECRETS_PASSPHRASE_FAILURE_MODE,
            value=payload.passphrase_failure_mode,
            actor=admin,
        )
        written.append(K.SECRETS_PASSPHRASE_FAILURE_MODE)
        metadata["passphrase_failure_mode"] = payload.passphrase_failure_mode
    metadata["keys"] = written
    record_audit_event(
        db,
        event_type=AuditEventType.secret_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="secret_policy",
        metadata=metadata,
        request=request,
    )
    db.commit()
    return get_secret_policy(db=db, _admin=admin)
