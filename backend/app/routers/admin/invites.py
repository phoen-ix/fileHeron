"""/api/admin/invites - list + revoke + regenerate + resend + activate."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.invite_token import InviteToken
from ...models.user import User
from ...schemas.admin import (
    ActivateInviteRequest,
    AdminInviteItem,
    AdminInviteListResponse,
    AdminUserItem,
    RegenerateInviteResponse,
    ResendInviteResponse,
)
from ...services import invite as invite_svc
from ...services import site as site_svc
from .users import _to_user_item

router = APIRouter()


def _to_invite_item(invite, inviter_name: str | None) -> AdminInviteItem:
    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    # v1.1.5: admin delete is now a hard delete, so no row in the list
    # can be in the legacy 'revoked' tombstone state. Only pending and
    # expired remain.
    state = "expired" if invite.expires_at <= now else "pending"
    return AdminInviteItem(
        id=invite.id,
        email=invite.email,
        target_role=invite.target_role,
        state=state,
        invited_by_id=invite.created_by_id,
        invited_by_display_name=inviter_name,
        initial_group_ids=invite.initial_group_ids,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
    )


@router.get("/invites", response_model=AdminInviteListResponse)
def list_invites(
    state: str = Query("all", pattern=r"^(pending|expired|all)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdminInviteListResponse:
    items, total = invite_svc.list_invites(
        db, state_filter=state, page=page, page_size=page_size
    )
    inviter_ids = {inv.created_by_id for inv in items if inv.created_by_id}
    inviter_names: dict[int, str] = {}
    if inviter_ids:
        rows = (
            db.query(User.id, User.display_name)
            .filter(User.id.in_(inviter_ids))
            .all()
        )
        inviter_names = {row[0]: row[1] for row in rows}
    return AdminInviteListResponse(
        items=[
            _to_invite_item(inv, inviter_names.get(inv.created_by_id))
            for inv in items
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(
    invite_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> Response:
    invite = db.query(InviteToken).filter(InviteToken.id == invite_id).one_or_none()
    if invite is None:
        raise AppError(404, "INVITE_NOT_FOUND", "Invite not found.")
    invite_svc.revoke_invite(db, invite=invite, actor=admin, request=request)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/invites/{invite_id}/regenerate", response_model=RegenerateInviteResponse
)
def regenerate_invite(
    invite_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> RegenerateInviteResponse:
    invite = db.query(InviteToken).filter(InviteToken.id == invite_id).one_or_none()
    if invite is None:
        raise AppError(404, "INVITE_NOT_FOUND", "Invite not found.")
    plaintext = invite_svc.regenerate_invite(
        db, invite=invite, actor=admin, request=request
    )
    db.commit()
    base = site_svc.get_site_url(db).rstrip("/")
    return RegenerateInviteResponse(
        token=plaintext,
        url=f"{base}/register/{plaintext}",
        expires_at=invite.expires_at,
    )


@router.post("/invites/{invite_id}/resend", response_model=ResendInviteResponse)
async def resend_invite(
    invite_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> ResendInviteResponse:
    invite = db.query(InviteToken).filter(InviteToken.id == invite_id).one_or_none()
    if invite is None:
        raise AppError(404, "INVITE_NOT_FOUND", "Invite not found.")
    new_expires = await invite_svc.resend_invite(
        db, invite=invite, actor=admin, request=request
    )
    db.commit()
    return ResendInviteResponse(ok=True, expires_at=new_expires)


@router.post(
    "/invites/{invite_id}/activate",
    response_model=AdminUserItem,
    status_code=status.HTTP_201_CREATED,
)
def activate_invite(
    invite_id: int,
    payload: ActivateInviteRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> AdminUserItem:
    invite = db.query(InviteToken).filter(InviteToken.id == invite_id).one_or_none()
    if invite is None:
        raise AppError(404, "INVITE_NOT_FOUND", "Invite not found.")
    user = invite_svc.activate_invite_as_admin(
        db,
        invite=invite,
        actor=admin,
        display_name=payload.display_name,
        locale=payload.locale,
        request=request,
    )
    db.commit()
    return _to_user_item(db, user)
