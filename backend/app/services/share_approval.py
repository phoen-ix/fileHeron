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
from collections.abc import Callable

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models.file import File, FileApprovalState, FileState
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
    """True if ``user`` may approve/reject pending shares. Admin always passes
    (operator escape hatch); otherwise the feature must be on, and the base mode
    plus the additive user/group allowlist decide.

    The admin check sits ABOVE the feature switch on purpose. Turning approval
    off does not un-queue the shares already waiting: their senders cannot
    withdraw them and nothing sweeps them, so with the switch checked first
    every in-flight share became permanently undecidable - files uploaded,
    quota charged, recipients never notified, and no way out but SQL (audit
    2026-07-30). An admin can still clear the queue after the switch is
    flipped."""
    if user.role == UserRole.admin:
        return True
    if not is_enabled(db):
        return False
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
    """True if the share REACHES at least one client, directly or via a group.

    The scope is `outbound_to_clients` - "does this leave the organisation" -
    so what matters is who receives it, not how they were addressed. This used
    to inner-join User on `recipient_user_id`, which is NULL on a group
    recipient row (`share.py` sets one or the other, never both), so the join
    silently dropped every group and the whole four-eyes policy did nothing for
    a share addressed to a group containing clients. The docstring said "direct
    recipient user", so the code was self-consistent - the SCOPE was what it got
    wrong.

    Group membership is resolved at query time, matching how
    `is_authorized_to_download` decides who can actually fetch the bytes.
    """
    direct = (
        db.query(ShareRecipient.recipient_user_id)
        .join(User, User.id == ShareRecipient.recipient_user_id)
        .filter(
            ShareRecipient.share_id == share.id,
            User.role == UserRole.client,
            User.is_disabled.is_(False),
        )
        .first()
    )
    if direct is not None:
        return True

    via_group = (
        db.query(GroupMember.user_id)
        .join(User, User.id == GroupMember.user_id)
        .join(
            ShareRecipient,
            ShareRecipient.recipient_group_id == GroupMember.group_id,
        )
        .filter(
            ShareRecipient.share_id == share.id,
            User.role == UserRole.client,
            User.is_disabled.is_(False),
        )
        .first()
    )
    return via_group is not None


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
        creator = share.created_by or db.get(User, share.created_by_id)
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
    echoes this value back and the decision is refused if it moved.

    The digest covers each file's CONTENT, not just its identity. Keying on the
    id alone made it stable across `uploading -> clean`: `create_pending` writes
    a row with a client-declared name and size before a single byte lands, so an
    approver could echo a perfectly matching fingerprint and still sign off on
    bytes that did not exist when they looked. Size, digest and state all move
    when the upload completes, so now it moves too."""
    parts = []
    for f in sorted(
        (f for f in share.files if f.state != FileState.deleted), key=lambda f: f.id
    ):
        parts.append(
            f"{f.id}:{f.size_bytes}:{f.sha256_hex or ''}:{f.state.value}:{f.approval_state.value}"
        )
    link_id = (
        db.query(PublicLink.id)
        .filter(PublicLink.share_id == share.id, PublicLink.revoked_at.is_(None))
        .scalar()
    )
    raw = "|".join([*parts, f"link={link_id or ''}"])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def can_review_added_files(db: Session, user: User) -> bool:
    """True if ``user`` may fetch the bytes of a file that was added to an
    already-approved share and is awaiting its own decision.

    Share-state-independent, unlike :func:`can_review_pending`: the share these
    files hang off is `active`, which is exactly why the share-level check
    cannot express this."""
    if not allow_content_review(db):
        return False
    return can_approve(db, user)


def can_review_this_share(db: Session, user: User, share: Share) -> bool:
    """True if ``user`` may fetch the BYTES of an ACTIVE share's files because
    it is carrying files that need THEIR decision.

    Content-review-dependent, and that is the whole difference between this and
    :func:`can_decide_added_files`. This one answers "may they see the
    content"; that one answers "may they open the share and cast the vote".
    Until v2.13.3 this was also the gate on opening the share, so an approver
    with content review off was refused the page they were emailed a link to.
    Do not fold the two together.

    Scoped to shares that actually have something awaiting review, deliberately.
    The unscoped version - "an approver may open any active share" - is a much
    larger grant than the workflow needs: it would hand every employee approver
    a view of every active outbound share in the instance, forever, rather than
    only while a decision is outstanding."""
    if not can_review_added_files(db, user):
        return False
    return bool(files_awaiting_review(db, share))


def can_decide_added_files(db: Session, user: User, share: Share) -> bool:
    """True if ``user`` may decide on the files appended to this already-
    approved share: an approver, not its creator, and something is actually
    awaiting a decision.

    The ACTIVE-share sibling of :func:`can_decide`, and the exact authorization
    triple ``share.decide_added_files`` enforces - it lives here so the right to
    OPEN the share and the right to DECIDE on it cannot drift apart again. They
    had: the view check went through :func:`can_review_this_share`, which
    requires ``allow_content_review``, while the decision endpoint never has. An
    approver with content review off was emailed a link to a page that 403'd
    them, and could still cast the vote blind over the API.

    Deliberately independent of ``allow_content_review``. That toggle says
    whether an approver may fetch the BYTES of a file awaiting review; it does
    not say whether they may look at the share they are being asked to sign off
    on. No `share.state` term and no `approval_was_required` fast path either:
    `decide_added_files` has neither, and an extra conjunct on a gate that also
    gates the DECISION is how files become permanently undecidable."""
    if user.id == share.created_by_id:
        return False
    if not can_approve(db, user):
        return False
    return bool(files_awaiting_review(db, share))


def files_awaiting_review(db: Session, share: Share) -> list[str]:
    """IDs of this share's files still waiting on a post-approval decision."""
    from ..models.file import File, FileApprovalState

    return [
        fid
        for (fid,) in db.query(File.id)
        .filter(
            File.share_id == share.id,
            File.approval_state == FileApprovalState.pending_review,
            File.state != FileState.deleted,
        )
        .order_by(File.created_at.asc(), File.id.asc())
        .all()
    ]


def can_review_pending(db: Session, user: User, share: Share) -> bool:
    """True if ``user`` may preview/download the files of a pending share for
    review (gated by the admin `allow_content_review` toggle + approver set)."""
    if share.state != ShareState.pending_approval:
        return False
    if not allow_content_review(db):
        return False
    return can_approve(db, user)


def has_pending_shares(db: Session) -> bool:
    """Whether any share is still waiting on a decision. Used to decide whether
    to surface the Approvals view at all: `can_approve` answers "may you
    decide", which is True for an admin even with the feature off, and that
    alone would put a permanent Approvals link in front of an instance that
    never turned approval on.

    Mirrors `list_pending_approvals`' filter: an ACTIVE share carrying files
    awaiting a post-approval decision is a queue item too. Counting only
    `pending_approval` hid the Approvals nav from the one operator who needed
    it - `approval_was_required` is a STORED, sticky fact, so switching the
    feature off does not stop a later upload into that still-active share being
    marked `pending_review`. Then nobody is notified (`approver_user_ids`
    returns an empty set with the feature off), the nav is dark, and the queue
    is quietly non-empty: the files are gated from recipients forever with no
    route in the UI to the decision that would release them. The admin escape
    hatch in `can_approve` exists for exactly that state; this makes its door
    visible."""
    awaiting = (
        db.query(File.share_id)
        .filter(
            File.approval_state == FileApprovalState.pending_review,
            File.state != FileState.deleted,
        )
        .distinct()
    )
    return (
        db.query(Share.id)
        .filter(
            or_(
                Share.state == ShareState.pending_approval,
                and_(
                    Share.state == ShareState.active,
                    Share.id.in_(awaiting.scalar_subquery()),
                ),
            )
        )
        .first()
        is not None
    )


def decider_for(db: Session, user: User) -> Callable[[Share], bool]:
    """:func:`can_decide` bound to one user for the length of a request.

    The approver policy does not vary by share, but resolving it costs four
    settings reads plus a membership query and `settings.get` is an uncached
    SELECT per key. A caller asking about a whole PAGE would otherwise pay that
    once per row. The approvals queue is the worst case: every row there is
    `pending_approval`, so the state short-circuit never fires, and it is the
    one page where every viewer is by definition an approver."""
    approver: list[bool] = []

    def _decide(share: Share) -> bool:
        if share.state != ShareState.pending_approval:
            return False
        if user.id == share.created_by_id:
            return False
        if not approver:
            approver.append(can_approve(db, user))
        return approver[0]

    return _decide


def can_decide(db: Session, user: User, share: Share) -> bool:
    """True if ``user`` may approve/reject *this* share now: an approver, the
    share is pending, and it isn't their own (no self-approval, ever)."""
    return decider_for(db, user)(share)
