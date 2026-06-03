"""Group CRUD + membership.

`name_normalized` is the lowercased mirror of `name` and the unique key —
two callers can't race-create groups whose names differ only by case.

Adding/removing a member calls into the connection service to keep the
shared_group connection rows in sync.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.group import Group
from ..models.group_member import GroupMember
from ..models.share import Share, ShareState
from ..models.share_recipient import ShareRecipient
from ..models.user import User
from .audit import record_audit_event
from .connection import recompute_shared_group_connections_for_user


def _normalize(name: str) -> str:
    return name.strip().lower()


def create_group(
    db: Session,
    *,
    actor: User,
    name: str,
    description: str | None,
    is_company_inbox: bool,
    request=None,
) -> Group:
    name_str = name.strip()
    if not name_str:
        raise AppError(400, "GROUP_NAME_REQUIRED", "Group name cannot be blank.")
    normalized = _normalize(name_str)
    if db.query(Group).filter(Group.name_normalized == normalized).one_or_none():
        raise AppError(409, "GROUP_NAME_TAKEN", "A group with that name already exists.")
    g = Group(
        name=name_str,
        name_normalized=normalized,
        description=description or None,
        is_company_inbox=is_company_inbox,
        created_by_id=actor.id,
    )
    db.add(g)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.group_created,
        actor_user_id=actor.id,
        target_type="group",
        target_id=g.id,
        metadata={"name": g.name, "is_company_inbox": is_company_inbox},
        request=request,
    )
    return g


def get_or_404(db: Session, group_id: int) -> Group:
    g = db.query(Group).filter(Group.id == group_id).one_or_none()
    if g is None:
        raise AppError(404, "GROUP_NOT_FOUND", "Group not found.")
    return g


def list_groups(db: Session) -> list[Group]:
    return db.query(Group).order_by(Group.name).all()


def update_group(
    db: Session,
    *,
    actor: User,
    group: Group,
    name: str | None = None,
    description: str | None = None,
    is_company_inbox: bool | None = None,
    request=None,
) -> Group:
    if name is not None:
        new_norm = _normalize(name)
        if new_norm != group.name_normalized:
            collision = (
                db.query(Group)
                .filter(Group.name_normalized == new_norm, Group.id != group.id)
                .one_or_none()
            )
            if collision is not None:
                raise AppError(
                    409, "GROUP_NAME_TAKEN", "A group with that name already exists."
                )
            group.name = name.strip()
            group.name_normalized = new_norm
    if description is not None:
        group.description = description or None
    if is_company_inbox is not None:
        group.is_company_inbox = is_company_inbox
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.group_updated,
        actor_user_id=actor.id,
        target_type="group",
        target_id=group.id,
        request=request,
    )
    return group


def delete_group(
    db: Session, *, actor: User, group: Group, request=None
) -> None:
    """Block deletion if the group is currently a recipient of any active
    share — admin must revoke those first. Default per Phase 4 policy."""
    active_shares = (
        db.query(Share.id)
        .join(ShareRecipient, ShareRecipient.share_id == Share.id)
        .filter(
            ShareRecipient.recipient_group_id == group.id,
            Share.state == ShareState.active,
        )
        .limit(1)
        .all()
    )
    if active_shares:
        raise AppError(
            409,
            "GROUP_IN_USE",
            "This group is the recipient of one or more active shares. "
            "Revoke or expire those first.",
        )

    # Capture members so we can recompute their connections after delete.
    member_users = [m.user for m in group.members]

    record_audit_event(
        db,
        event_type=AuditEventType.group_deleted,
        actor_user_id=actor.id,
        target_type="group",
        target_id=group.id,
        metadata={"name": group.name},
        request=request,
    )
    db.delete(group)
    db.flush()

    for u in member_users:
        recompute_shared_group_connections_for_user(db, user=u)


def add_member(
    db: Session, *, actor: User, group: Group, user: User, request=None
) -> GroupMember:
    if user.is_disabled:
        raise AppError(409, "USER_DISABLED", "Cannot add a disabled user.")
    existing = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group.id, GroupMember.user_id == user.id)
        .one_or_none()
    )
    if existing is not None:
        return existing
    m = GroupMember(group_id=group.id, user_id=user.id)
    db.add(m)
    db.flush()
    recompute_shared_group_connections_for_user(db, user=user)
    record_audit_event(
        db,
        event_type=AuditEventType.group_member_added,
        actor_user_id=actor.id,
        target_type="group",
        target_id=group.id,
        metadata={"user_id": user.id},
        request=request,
    )
    return m


def remove_member(
    db: Session, *, actor: User, group: Group, user: User, request=None
) -> None:
    m = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group.id, GroupMember.user_id == user.id)
        .one_or_none()
    )
    if m is None:
        raise AppError(404, "MEMBER_NOT_FOUND", "User is not a member of this group.")
    db.delete(m)
    db.flush()
    recompute_shared_group_connections_for_user(db, user=user)
    record_audit_event(
        db,
        event_type=AuditEventType.group_member_removed,
        actor_user_id=actor.id,
        target_type="group",
        target_id=group.id,
        metadata={"user_id": user.id},
        request=request,
    )
