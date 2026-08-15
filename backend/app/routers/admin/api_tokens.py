"""/api/admin/api-tokens + /api/admin/settings/api-tokens/policy."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.api_token import ApiToken
from ...models.audit_log import AuditEventType
from ...models.group import Group
from ...models.user import User
from ...schemas.api_token import (
    AdminApiTokenItem,
    AdminApiTokenListResponse,
    AdminCreateApiTokenRequest,
    AllowedGroupItem,
    AllowedUserItem,
    CreateApiTokenResponse,
    TokenPolicyResponse,
    UpdateTokenPolicyRequest,
)
from ...services import api_token as api_token_svc
from ...services import settings as settings_svc
from ...services.audit import record_audit_event
from ...utils.timeutil import utc_now

router = APIRouter()


def _token_status(t: ApiToken) -> str:
    if t.revoked_at is not None:
        return "revoked"
    if t.expires_at is not None and utc_now() > t.expires_at:
        return "expired"
    if t.disabled_at is not None:
        return "disabled"
    return "active"


def _to_admin_token_item(t: ApiToken, owner: User) -> AdminApiTokenItem:
    return AdminApiTokenItem(
        id=t.id,
        name=t.name,
        last4=t.last4,
        owner_user_id=owner.id,
        owner_display_name=owner.display_name,
        owner_email=owner.email,
        owner_role=owner.role.value,
        status=_token_status(t),
        created_at=t.created_at,
        last_used_at=t.last_used_at,
        revoked_at=t.revoked_at,
        disabled_at=t.disabled_at,
        expires_at=t.expires_at,
        scopes=t.scopes_list,
    )


@router.get("/settings/api-tokens/policy", response_model=TokenPolicyResponse)
def get_token_policy(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> TokenPolicyResponse:
    mode, user_ids, group_ids = api_token_svc._resolve_policy(db)
    users = db.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
    groups = db.query(Group).filter(Group.id.in_(group_ids)).all() if group_ids else []
    return TokenPolicyResponse(
        mode=mode,  # type: ignore[arg-type]
        allowed_user_ids=user_ids,
        allowed_group_ids=group_ids,
        allowed_users=[
            AllowedUserItem(
                id=u.id,
                display_name=u.display_name,
                email=u.email,
                role=u.role.value,
            )
            for u in users
        ],
        allowed_groups=[AllowedGroupItem(id=g.id, name=g.name) for g in groups],
    )


@router.put("/settings/api-tokens/policy", response_model=TokenPolicyResponse)
def update_token_policy(
    payload: UpdateTokenPolicyRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> TokenPolicyResponse:
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
        key=settings_svc.Keys.API_TOKEN_POLICY_MODE,
        value=payload.mode,
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.API_TOKEN_ALLOWED_USERS,
        value=json.dumps(payload.allowed_user_ids) if payload.allowed_user_ids else None,
        actor=admin,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.API_TOKEN_ALLOWED_GROUPS,
        value=json.dumps(payload.allowed_group_ids) if payload.allowed_group_ids else None,
        actor=admin,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.api_policy_changed,
        actor_user_id=admin.id,
        target_type="settings",
        target_id="api_token_policy",
        metadata={
            "mode": payload.mode,
            "user_count": len(payload.allowed_user_ids),
            "group_count": len(payload.allowed_group_ids),
        },
        request=request,
    )
    db.commit()
    return get_token_policy(db=db, _admin=admin)


@router.get("/api-tokens", response_model=AdminApiTokenListResponse)
def admin_list_api_tokens(
    q: str = Query("", max_length=120),
    owner_id: int | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdminApiTokenListResponse:
    rows, total = api_token_svc.list_all_tokens(
        db,
        q=q,
        owner_id=owner_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    owner_ids = list({r.owner_user_id for r in rows})
    owners = (
        {u.id: u for u in db.query(User).filter(User.id.in_(owner_ids)).all()}
        if owner_ids
        else {}
    )
    items = [_to_admin_token_item(r, owners[r.owner_user_id]) for r in rows]
    return AdminApiTokenListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


@router.post(
    "/api-tokens",
    response_model=CreateApiTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def admin_create_api_token(
    payload: AdminCreateApiTokenRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> CreateApiTokenResponse:
    from ...services.step_up import verify_password_or_403

    verify_password_or_403(db, admin, payload.password, request=request)
    target = (
        db.query(User).filter(User.id == payload.target_user_id).one_or_none()
    )
    if target is None:
        raise AppError(404, "USER_NOT_FOUND", "Target user not found.")
    if target.is_disabled:
        raise AppError(
            409, "USER_DISABLED", "Cannot create a token for a disabled user."
        )
    expires_at = api_token_svc.normalize_expiry(payload.expires_at)
    scopes = api_token_svc.normalize_scopes(payload.scopes)
    record, plaintext = api_token_svc.admin_create_for(
        db,
        actor=admin,
        target_user=target,
        name=payload.name,
        expires_at=expires_at,
        scopes=scopes,
        request=request,
    )
    db.commit()
    return CreateApiTokenResponse(
        id=record.id,
        name=record.name,
        last4=record.last4,
        plaintext_token=plaintext,
        created_at=record.created_at,
        expires_at=record.expires_at,
        scopes=record.scopes_list,
        owner_user_id=record.owner_user_id,
        owner_display_name=target.display_name,
    )


@router.post(
    "/api-tokens/{token_id}/disable", response_model=AdminApiTokenItem
)
def admin_disable_api_token(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> AdminApiTokenItem:
    record = api_token_svc.disable_token(
        db, actor=admin, token_id=token_id, request=request
    )
    db.commit()
    owner = db.query(User).filter(User.id == record.owner_user_id).one()
    return _to_admin_token_item(record, owner)


@router.post(
    "/api-tokens/{token_id}/reactivate", response_model=AdminApiTokenItem
)
def admin_reactivate_api_token(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> AdminApiTokenItem:
    record = api_token_svc.reactivate_token(
        db, actor=admin, token_id=token_id, request=request
    )
    db.commit()
    owner = db.query(User).filter(User.id == record.owner_user_id).one()
    return _to_admin_token_item(record, owner)


@router.delete(
    "/api-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT
)
def admin_revoke_api_token(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> Response:
    api_token_svc.admin_revoke_token(
        db, actor=admin, token_id=token_id, request=request
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
