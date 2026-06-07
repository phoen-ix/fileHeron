"""Groups CRUD + member management.

CRUD itself is admin-only. The `/recipient-targets` endpoint is open to
any authenticated user - it returns the groups they can target as a
share recipient (clients see company-inbox groups; employees see their
memberships + company-inbox groups; admins see everything).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from ..dependencies import get_current_admin, get_db, require_scope
from ..middleware.errors import AppError
from ..models.group import Group
from ..models.group_member import GroupMember
from ..models.user import User, UserRole
from ..schemas.group import (
    AddGroupMembersRequest,
    CreateGroupRequest,
    GroupDetailResponse,
    GroupListResponse,
    GroupMemberItem,
    GroupResponse,
    UpdateGroupRequest,
)
from ..services import group as group_svc

router = APIRouter(prefix="/api/groups", tags=["groups"])


def _to_response(group, member_count: int) -> GroupResponse:
    return GroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        is_company_inbox=group.is_company_inbox,
        created_at=group.created_at,
        created_by_id=group.created_by_id,
        member_count=member_count,
    )


def _detail_response(db: Session, group) -> GroupDetailResponse:
    members = (
        db.query(GroupMember)
        .options(selectinload(GroupMember.user))
        .filter(GroupMember.group_id == group.id)
        .all()
    )
    items = [
        GroupMemberItem(
            user_id=m.user.id,
            display_name=m.user.display_name,
            email=m.user.email,
            role=m.user.role.value,
            joined_at=m.joined_at,
        )
        for m in members
    ]
    return GroupDetailResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        is_company_inbox=group.is_company_inbox,
        created_at=group.created_at,
        created_by_id=group.created_by_id,
        member_count=len(items),
        members=items,
    )


@router.get("/recipient-targets", response_model=GroupListResponse)
def recipient_targets(
    db: Session = Depends(get_db),
    me: User = Depends(require_scope("shares:read")),
) -> GroupListResponse:
    """Groups the caller can target in a share. Open to all roles."""
    if me.role == UserRole.admin:
        groups = db.query(Group).order_by(Group.name).all()
    elif me.role == UserRole.employee:
        member_group_ids = (
            db.query(GroupMember.group_id)
            .filter(GroupMember.user_id == me.id)
            .all()
        )
        ids = [g[0] for g in member_group_ids]
        # Always include company_inbox groups regardless of membership.
        groups = (
            db.query(Group)
            .filter((Group.id.in_(ids)) | (Group.is_company_inbox.is_(True)))
            .order_by(Group.name)
            .all()
        )
    elif me.role == UserRole.client:
        groups = (
            db.query(Group)
            .filter(Group.is_company_inbox.is_(True))
            .order_by(Group.name)
            .all()
        )
    else:
        groups = []
    counts = _count_members_bulk(db, [g.id for g in groups])
    items = [_to_response(g, counts.get(g.id, 0)) for g in groups]
    return GroupListResponse(items=items)


def _count_members(db: Session, group_id: int) -> int:
    return db.query(GroupMember).filter(GroupMember.group_id == group_id).count()


def _count_members_bulk(db: Session, group_ids: list[int]) -> dict[int, int]:
    """Member counts for a set of groups in one query (was a COUNT per group)."""
    if not group_ids:
        return {}
    rows = (
        db.query(GroupMember.group_id, func.count())
        .filter(GroupMember.group_id.in_(group_ids))
        .group_by(GroupMember.group_id)
        .all()
    )
    return {gid: int(n) for gid, n in rows}


@router.get("", response_model=GroupListResponse)
def list_groups_endpoint(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> GroupListResponse:
    groups = group_svc.list_groups(db)
    counts = _count_members_bulk(db, [g.id for g in groups])
    items = [_to_response(g, counts.get(g.id, 0)) for g in groups]
    return GroupListResponse(items=items)


@router.post("", response_model=GroupResponse, status_code=201)
def create_group_endpoint(
    payload: CreateGroupRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> GroupResponse:
    g = group_svc.create_group(
        db,
        actor=admin,
        name=payload.name,
        description=payload.description,
        is_company_inbox=payload.is_company_inbox,
        request=request,
    )
    db.commit()
    return _to_response(g, member_count=0)


@router.get("/{group_id}", response_model=GroupDetailResponse)
def get_group_endpoint(
    group_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> GroupDetailResponse:
    g = group_svc.get_or_404(db, group_id)
    return _detail_response(db, g)


@router.patch("/{group_id}", response_model=GroupResponse)
def update_group_endpoint(
    group_id: int,
    payload: UpdateGroupRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> GroupResponse:
    g = group_svc.get_or_404(db, group_id)
    g = group_svc.update_group(
        db,
        actor=admin,
        group=g,
        name=payload.name,
        description=payload.description,
        is_company_inbox=payload.is_company_inbox,
        request=request,
    )
    db.commit()
    member_count = db.query(GroupMember).filter(GroupMember.group_id == g.id).count()
    return _to_response(g, member_count)


@router.delete("/{group_id}", status_code=204)
def delete_group_endpoint(
    group_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> None:
    g = group_svc.get_or_404(db, group_id)
    group_svc.delete_group(db, actor=admin, group=g, request=request)
    db.commit()


@router.post("/{group_id}/members", response_model=GroupDetailResponse)
def add_members_endpoint(
    group_id: int,
    payload: AddGroupMembersRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> GroupDetailResponse:
    g = group_svc.get_or_404(db, group_id)
    for uid in payload.user_ids:
        u = db.query(User).filter(User.id == uid).one_or_none()
        if u is None:
            raise AppError(404, "USER_NOT_FOUND", f"User {uid} does not exist.")
        group_svc.add_member(db, actor=admin, group=g, user=u, request=request)
    db.commit()
    return _detail_response(db, g)


@router.delete("/{group_id}/members/{user_id}", status_code=204)
def remove_member_endpoint(
    group_id: int,
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> None:
    g = group_svc.get_or_404(db, group_id)
    u = db.query(User).filter(User.id == user_id).one_or_none()
    if u is None:
        raise AppError(404, "USER_NOT_FOUND", f"User {user_id} does not exist.")
    group_svc.remove_member(db, actor=admin, group=g, user=u, request=request)
    db.commit()
