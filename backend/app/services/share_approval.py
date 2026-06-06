"""Share-approval workflow policy (v1.24.0).

Admin-tunable, all read live from ``app_settings`` (no boot cache):
- whether approval is required at all (master switch),
- **who may approve** - `admins_only` (default) or `employees_admins`, plus an
  additive user/group allowlist (the "special group"); admins always pass,
- **which shares** are in scope (`outbound` / `all` / `outbound_to_clients`),
- whether an approver's **own** shares are exempt (auto-approved),
- whether approvers may **review file contents** of a pending share.

The approver set mirrors ``policy_gate`` but defaults to the *restrictive*
`admins_only` (policy_gate's own default is the permissive `everyone`, which is
wrong for an approval gate), so we resolve it here.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from ..models.group_member import GroupMember
from ..models.share import Share, ShareKind, ShareState
from ..models.share_recipient import ShareRecipient
from ..models.user import User, UserRole
from . import settings as settings_svc

APPROVER_MODES = ("admins_only", "employees_admins")
SCOPES = ("outbound", "all", "outbound_to_clients")


def _parse_id_list(raw: str | None) -> list[int]:
    try:
        return [int(x) for x in json.loads(raw or "[]")]
    except (ValueError, TypeError):
        return []


def is_enabled(db: Session) -> bool:
    return settings_svc.get_bool(
        db, settings_svc.Keys.SHARE_APPROVAL_ENABLED, default=False
    )


def effective_mode(db: Session) -> str:
    mode = settings_svc.get(db, settings_svc.Keys.SHARE_APPROVAL_APPROVER_MODE)
    return mode if mode in APPROVER_MODES else "admins_only"


def effective_scope(db: Session) -> str:
    scope = settings_svc.get(db, settings_svc.Keys.SHARE_APPROVAL_SCOPE)
    return scope if scope in SCOPES else "outbound"


def allow_content_review(db: Session) -> bool:
    return settings_svc.get_bool(
        db, settings_svc.Keys.SHARE_APPROVAL_ALLOW_CONTENT_REVIEW, default=True
    )


def exempt_approvers(db: Session) -> bool:
    return settings_svc.get_bool(
        db, settings_svc.Keys.SHARE_APPROVAL_EXEMPT_APPROVERS, default=True
    )


def resolve_approver_policy(db: Session) -> tuple[str, list[int], list[int]]:
    return (
        effective_mode(db),
        _parse_id_list(settings_svc.get(db, settings_svc.Keys.SHARE_APPROVAL_APPROVER_USERS)),
        _parse_id_list(settings_svc.get(db, settings_svc.Keys.SHARE_APPROVAL_APPROVER_GROUPS)),
    )


def can_approve(db: Session, user: User) -> bool:
    """True if ``user`` may approve/reject pending shares. False when the feature
    is off. Admin always passes (operator escape hatch); otherwise the base mode
    plus the additive user/group allowlist decide."""
    if not is_enabled(db):
        return False
    if user.role == UserRole.admin:
        return True
    mode, allowed_users, allowed_groups = resolve_approver_policy(db)
    if mode == "employees_admins" and user.role == UserRole.employee:
        return True
    if user.id in allowed_users:
        return True
    if allowed_groups:
        hit = (
            db.query(GroupMember.user_id)
            .filter(
                GroupMember.user_id == user.id,
                GroupMember.group_id.in_(allowed_groups),
            )
            .first()
        )
        if hit is not None:
            return True
    return False


def approver_user_ids(db: Session) -> set[int]:
    """Every non-disabled user who may approve - used to fan out the
    `share_pending_approval` notification. Empty when the feature is off."""
    if not is_enabled(db):
        return set()
    mode, allowed_users, allowed_groups = resolve_approver_policy(db)
    roles = [UserRole.admin]
    if mode == "employees_admins":
        roles.append(UserRole.employee)
    ids: set[int] = {
        uid
        for (uid,) in db.query(User.id)
        .filter(User.role.in_(roles), User.is_disabled.is_(False))
        .all()
    }
    if allowed_users:
        ids.update(
            uid
            for (uid,) in db.query(User.id)
            .filter(User.id.in_(allowed_users), User.is_disabled.is_(False))
            .all()
        )
    if allowed_groups:
        ids.update(
            uid
            for (uid,) in db.query(GroupMember.user_id)
            .join(User, User.id == GroupMember.user_id)
            .filter(
                GroupMember.group_id.in_(allowed_groups),
                User.is_disabled.is_(False),
            )
            .all()
        )
    return ids


def _has_client_recipient(db: Session, share: Share) -> bool:
    """True if the share has at least one direct recipient user with the client
    role (the test for the `outbound_to_clients` scope)."""
    hit = (
        db.query(ShareRecipient.recipient_user_id)
        .join(User, User.id == ShareRecipient.recipient_user_id)
        .filter(
            ShareRecipient.share_id == share.id,
            User.role == UserRole.client,
        )
        .first()
    )
    return hit is not None


def is_approval_required(db: Session, share: Share) -> bool:
    """Whether this share must be approved before it goes live. Call AFTER the
    share's recipient rows are flushed (scope `outbound_to_clients` reads them)."""
    if not is_enabled(db):
        return False
    scope = effective_scope(db)
    if share.kind == ShareKind.inbound and scope != "all":
        return False
    if scope == "outbound_to_clients" and not _has_client_recipient(db, share):
        return False
    if exempt_approvers(db):
        creator = share.created_by or db.query(User).get(share.created_by_id)
        if creator is not None and can_approve(db, creator):
            return False
    return True


def can_review_pending(db: Session, user: User, share: Share) -> bool:
    """True if ``user`` may preview/download the files of a pending share for
    review (gated by the admin `allow_content_review` toggle + approver set)."""
    if share.state != ShareState.pending_approval:
        return False
    if not allow_content_review(db):
        return False
    return can_approve(db, user)


def can_decide(db: Session, user: User, share: Share) -> bool:
    """True if ``user`` may approve/reject *this* share now: an approver, the
    share is pending, and it isn't their own (no self-approval, ever)."""
    return (
        share.state == ShareState.pending_approval
        and user.id != share.created_by_id
        and can_approve(db, user)
    )
