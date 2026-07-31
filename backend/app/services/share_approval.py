"""Share-approval workflow policy (v1.24.0).

Admin-tunable, all read live from ``app_settings`` (no boot cache):
- whether approval is required at all (master switch),
- **who may approve** - `admins_only` (default) or `employees_admins`, plus an
  additive user/group allowlist (the "special group"); admins always pass,
- **which shares** are in scope (`outbound` / `all` / `outbound_to_clients`),
- whether an approver's **own** shares are exempt (auto-approved),
- whether approvers may **review file contents** of a pending share.

The approver set mirrors ``policy_gate``'s shape but resolves its mode here rather
than through the shared gate: ``policy_gate.DEFAULT_POLICY_MODE`` is
`employees_admins`, which for an approval gate would make every employee an
approver on an unconfigured deploy, so this module defaults to `admins_only`.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from ..models.file import FileState
from ..models.group_member import GroupMember
from ..models.public_link import PublicLink
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


def policy_is_inert(mode: str, scope: str, exempt: bool) -> bool:
    """True when a policy combination guarantees that NO share can ever require
    approval - a four-eyes control that silently does nothing.

    ``employees_admins`` makes every employee an approver, and
    ``exempt_approvers`` auto-approves an approver's own shares. Share kind is
    derived from role (staff create outbound, clients inbound), so every
    outbound share is created by an approver and exempted at birth. Unless
    inbound shares are also in scope, there is nothing left to queue.

    Structural, not data-dependent: no amount of adding, removing or disabling
    users changes it. The additive allowlist can produce the same effect if it
    happens to cover every employee, but that is a property of the current user
    table rather than of the policy, so it is not asserted here (audit
    2026-07-30)."""
    return mode == "employees_admins" and exempt and scope != "all"


def is_inert(db: Session) -> bool:
    """Live-settings form of :func:`policy_is_inert`. False when the feature is
    off - a disabled control is honestly disabled, not silently inert."""
    if not is_enabled(db):
        return False
    return policy_is_inert(effective_mode(db), effective_scope(db), exempt_approvers(db))


def content_fingerprint(db: Session, share: Share) -> str:
    """Digest of what an approver is actually signing off on: the live file set
    plus whether a public link is attached.

    The owner may keep uploading into a pending share by design, and
    ``approve_share`` re-checks only the state - so a file added after the
    approver opened the review page shipped on approve. The approver's client
    echoes this value back and the decision is refused if it moved."""
    file_ids = sorted(f.id for f in share.files if f.state != FileState.deleted)
    link_id = (
        db.query(PublicLink.id)
        .filter(PublicLink.share_id == share.id, PublicLink.revoked_at.is_(None))
        .scalar()
    )
    raw = "|".join([*(str(i) for i in file_ids), f"link={link_id or ''}"])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


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
