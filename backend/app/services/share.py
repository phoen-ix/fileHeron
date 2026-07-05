"""Share lifecycle.

Phase 4 model: a share has N user-recipients and M group-recipients.
Inbox resolution is dynamic - group membership at query time decides
whether a user sees a group-targeted share. Authorization to download
follows the same rule.

Authorization to download:
- The sender (creator) can always download
- Admins can always download
- Users in `share_recipients.recipient_user_id` can download
- Users who are currently a member of any group in
  `share_recipients.recipient_group_id` can download

Authorization to send (kind=outbound, employee/admin → client(s)):
- Admins: any client + any group
- Employees: only clients they're connected to + groups they're a
  member of (or any company_inbox group, since those are the org-wide
  landing zone)

Authorization to send (kind=inbound, client → employee(s)/inbox group):
- The recipient employees must be connected to the sender
- Group recipients must be company_inbox groups
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session, joinedload

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.client_employee_connection import ClientEmployeeConnection
from ..models.group import Group
from ..models.group_member import GroupMember
from ..models.share import Share, ShareKind, ShareState
from ..models.share_recipient import ShareRecipient
from ..models.user import User, UserRole
from ..utils.timeutil import utc_now
from .audit import record_audit_event

logger = logging.getLogger("fileheron.share")




def _user_group_ids(db: Session, user_id: int) -> list[int]:
    rows = (
        db.query(GroupMember.group_id)
        .filter(GroupMember.user_id == user_id)
        .all()
    )
    return [r[0] for r in rows]


def _connected_client_ids_of(db: Session, employee_id: int) -> set[int]:
    rows = (
        db.query(ClientEmployeeConnection.client_user_id)
        .filter(ClientEmployeeConnection.employee_user_id == employee_id)
        .distinct()
        .all()
    )
    return {r[0] for r in rows}


def _validate_outbound_targets(
    db: Session, sender: User, users: list[User], groups: list[Group]
) -> None:
    if sender.role == UserRole.client:
        raise AppError(
            403, "FORBIDDEN_KIND", "Clients cannot send outbound shares."
        )
    # Admin: anything goes (within sanity).
    if sender.role == UserRole.admin:
        return
    # Employee: target users must be connected clients (or any user not a
    # client at all, e.g. another employee - also allowed). Groups must be
    # ones the employee is a member of OR a company_inbox group.
    connected = _connected_client_ids_of(db, sender.id)
    for u in users:
        if u.role == UserRole.client and u.id not in connected:
            raise AppError(
                403,
                "RECIPIENT_NOT_CONNECTED",
                f"You're not connected to user {u.id}.",
            )
    employee_groups = set(_user_group_ids(db, sender.id))
    for g in groups:
        if g.is_company_inbox:
            continue
        if g.id not in employee_groups:
            raise AppError(
                403,
                "GROUP_NOT_MEMBER",
                "You can only target groups you're a member of.",
            )


def _shares_group(db: Session, user_a_id: int, user_b_id: int) -> bool:
    """True if the two users are members of at least one common group. Used for
    client group-peer visibility of inbound (client→company) submissions."""
    a_groups = set(_user_group_ids(db, user_a_id))
    if not a_groups:
        return False
    return bool(a_groups & set(_user_group_ids(db, user_b_id)))


def _resolved_notify_flag(db: Session, notify_recipients: bool | None) -> bool:
    """The effective 'notify recipients' choice - the explicit per-share flag,
    else the admin default. Frozen onto a pending share so the deferred
    share_created (fired on approval) honours it."""
    if notify_recipients is None:
        from . import settings as settings_svc
        return settings_svc.get_bool(
            db, settings_svc.Keys.SHARE_NOTIFY_RECIPIENTS_DEFAULT, default=True
        )
    return notify_recipients


def _recipient_notify_ids(
    db: Session, share: Share, *, notify_recipients: bool
) -> set[int]:
    """User IDs to notify for a share_created event, resolved from the share's
    persisted recipient rows + kind. Sender excluded. Inbound notifies all
    non-disabled staff regardless of the flag."""
    if share.kind == ShareKind.inbound:
        rows = (
            db.query(User.id)
            .filter(
                User.role.in_([UserRole.employee, UserRole.admin]),
                User.is_disabled.is_(False),
            )
            .all()
        )
        ids = {uid for (uid,) in rows}
    else:
        if not notify_recipients:
            return set()
        recs = (
            db.query(ShareRecipient)
            .filter(ShareRecipient.share_id == share.id)
            .all()
        )
        ids = {r.recipient_user_id for r in recs if r.recipient_user_id is not None}
        group_ids = [r.recipient_group_id for r in recs if r.recipient_group_id is not None]
        if group_ids:
            member_rows = (
                db.query(GroupMember.user_id)
                .join(User, User.id == GroupMember.user_id)
                .filter(
                    GroupMember.group_id.in_(group_ids),
                    User.is_disabled.is_(False),
                )
                .all()
            )
            ids.update(uid for (uid,) in member_rows)
    ids.discard(share.created_by_id)
    return ids


def _dispatch_share_created(
    db: Session, share: Share, *, notify_recipients: bool
) -> None:
    """Fan a share_created notification to the share's recipients. Used by the
    active-on-create path AND the approval path (deferred until approve)."""
    from ..models.file import FileState
    from ..models.notification import NotificationCategory
    from . import notification as notif_svc
    from . import site as site_svc

    notify_user_ids = _recipient_notify_ids(
        db, share, notify_recipients=notify_recipients
    )
    if not notify_user_ids:
        return
    base_url = site_svc.get_site_url(db)
    sender = share.created_by or db.query(User).get(share.created_by_id)
    payload_base = {
        "sender_name": sender.display_name if sender else "",
        "subject": share.subject,
        "message": share.message,
        "expires_at": share.expires_at,
        "file_count": sum(1 for f in share.files if f.state != FileState.deleted),
        "share_url": f"{base_url}/share/{share.id}",
    }
    recipients = db.query(User).filter(User.id.in_(notify_user_ids)).all()
    for u in recipients:
        payload = dict(payload_base)
        payload["recipient_name"] = u.display_name
        notif_svc.dispatch(
            db,
            user=u,
            category=NotificationCategory.share_created,
            payload=payload,
            link_url=payload["share_url"],
            email_to=u.email,
        )


def _notify_approvers_pending(db: Session, share: Share) -> None:
    """Fan a share_pending_approval notification to every eligible approver
    (minus the creator)."""
    from ..models.notification import NotificationCategory
    from . import notification as notif_svc
    from . import share_approval as approval_svc
    from . import site as site_svc

    approver_ids = approval_svc.approver_user_ids(db)
    approver_ids.discard(share.created_by_id)
    if not approver_ids:
        return
    base_url = site_svc.get_site_url(db)
    sender = share.created_by or db.query(User).get(share.created_by_id)
    payload_base = {
        "sender_name": sender.display_name if sender else "",
        "subject": share.subject,
        "share_url": f"{base_url}/share/{share.id}",
    }
    approvers = db.query(User).filter(User.id.in_(approver_ids)).all()
    for u in approvers:
        payload = dict(payload_base)
        payload["recipient_name"] = u.display_name
        notif_svc.dispatch(
            db,
            user=u,
            category=NotificationCategory.share_pending_approval,
            payload=payload,
            link_url=payload["share_url"],
            email_to=u.email,
        )


def _notify_share_decision(
    db: Session, share: Share, *, category, reason: str | None
) -> None:
    """Tell the creator their share was approved / rejected."""
    from . import notification as notif_svc
    from . import site as site_svc

    creator = share.created_by or db.query(User).get(share.created_by_id)
    if creator is None:
        return
    base_url = site_svc.get_site_url(db)
    payload = {
        "recipient_name": creator.display_name,
        "subject": share.subject,
        "share_url": f"{base_url}/share/{share.id}",
    }
    if reason:
        payload["reason"] = reason
    notif_svc.dispatch(
        db,
        user=creator,
        category=category,
        payload=payload,
        link_url=payload["share_url"],
        email_to=creator.email,
    )


def create_share(
    db: Session,
    *,
    created_by: User,
    kind: ShareKind,
    expires_at: datetime | None,
    recipient_user_ids: list[int] | None = None,
    recipient_group_ids: list[int] | None = None,
    subject: str | None = None,
    message: str | None = None,
    allow_no_recipients: bool = False,
    notify_recipients: bool | None = None,
    download_limit: int | None = None,
    request=None,
) -> Share:
    # expires_at = None means "never expires" (v1.1.4). Otherwise:
    # Pydantic preserves tz info from ISO strings ending in Z / +HH:MM
    # (which is what dayjs.toISOString() produces on the frontend).
    # Normalise to naive UTC before comparing or storing - mirrors the
    # convention every other write site follows. See update_expiry()
    # below for the same guard at the PATCH boundary.
    if expires_at is not None:
        if expires_at.tzinfo is not None:
            expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
        if expires_at < utc_now():
            raise AppError(400, "EXPIRY_IN_PAST", "Expiry must be in the future.")

    # Inbound (client → the company): the client never picks recipients. The
    # audience - every employee/admin, plus the creator's group-peers - is
    # resolved at read time (see is_authorized_to_download / list_shares_for_user),
    # so no recipient rows are written and any client-supplied recipients are
    # ignored. Outbound (staff → client/group) resolves + validates recipients
    # as before.
    users: list[User] = []
    groups: list[Group] = []
    if kind == ShareKind.outbound:
        user_ids = list(dict.fromkeys(recipient_user_ids or []))  # dedupe, keep order
        group_ids = list(dict.fromkeys(recipient_group_ids or []))
        # Empty recipients are only OK when the caller explicitly opted in
        # (e.g., the router did so because an inline public link is being
        # attached - the link is the access path).
        if not user_ids and not group_ids and not allow_no_recipients:
            raise AppError(
                400, "NO_RECIPIENTS", "At least one user or group recipient is required."
            )
        # Bulk-load recipients (was one query per id), then validate in the
        # caller-supplied order so error messages still name the exact id.
        users_by_id = (
            {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()}
            if user_ids
            else {}
        )
        for uid in user_ids:
            u = users_by_id.get(uid)
            if u is None or u.is_disabled:
                raise AppError(
                    404, "RECIPIENT_NOT_FOUND", f"Recipient user {uid} is not available."
                )
            if u.id == created_by.id:
                raise AppError(400, "SELF_SHARE", "Cannot share with yourself.")
            users.append(u)
        groups_by_id = (
            {g.id: g for g in db.query(Group).filter(Group.id.in_(group_ids)).all()}
            if group_ids
            else {}
        )
        for gid in group_ids:
            g = groups_by_id.get(gid)
            if g is None:
                raise AppError(
                    404, "GROUP_NOT_FOUND", f"Recipient group {gid} is not available."
                )
            groups.append(g)
        _validate_outbound_targets(db, created_by, users, groups)

    share = Share(
        created_by_id=created_by.id,
        kind=kind,
        subject=(subject or "")[:255] or None,
        message=message or None,
        expires_at=expires_at,
        state=ShareState.active,
        download_limit=download_limit,
        # Mirror limit → remaining on create. Subsequent edits via
        # update_share_limit recompute remaining = max(0, new_limit - used).
        downloads_remaining=download_limit,
    )
    db.add(share)
    db.flush()

    for u in users:
        db.add(ShareRecipient(share_id=share.id, recipient_user_id=u.id))
    for g in groups:
        db.add(ShareRecipient(share_id=share.id, recipient_group_id=g.id))
    db.flush()

    audit_meta = {
        "kind": kind.value,
        "recipient_user_ids": [u.id for u in users],
        "recipient_group_ids": [g.id for g in groups],
    }
    resolved_notify = _resolved_notify_flag(db, notify_recipients)

    from . import share_approval as approval_svc

    if approval_svc.is_approval_required(db, share):
        # Hold the share for review: freeze the notify choice, ping the
        # approvers, and DON'T notify recipients yet - that happens on approval.
        share.state = ShareState.pending_approval
        share.notify_on_activation = resolved_notify
        db.flush()
        record_audit_event(
            db,
            event_type=AuditEventType.share_submitted_for_approval,
            actor_user_id=created_by.id,
            target_type="share",
            target_id=share.id,
            metadata=audit_meta,
            request=request,
        )
        _notify_approvers_pending(db, share)
        return share

    record_audit_event(
        db,
        event_type=AuditEventType.share_created,
        actor_user_id=created_by.id,
        target_type="share",
        target_id=share.id,
        metadata=audit_meta,
        request=request,
    )
    # Inbound notifies all staff regardless of the flag; outbound honours it.
    # Each recipient still applies their own per-category preference.
    _dispatch_share_created(db, share, notify_recipients=resolved_notify)
    return share


def get_share_or_404(db: Session, share_id: str) -> Share:
    share = db.query(Share).filter(Share.id == share_id).one_or_none()
    if share is None:
        raise AppError(404, "SHARE_NOT_FOUND", "Share not found.")
    return share


def assert_share_downloadable(share: Share) -> None:
    """Gate downloads on share lifecycle state. Mirrors the public path's
    `public_link.assert_link_usable` so the authenticated download path
    honours revoke / expire just like public links do.

    Without this, `revoke_share` (which only flips `state`, leaving the
    bytes + `file.state` intact) is a no-op for authorised recipients:
    they can keep minting signed URLs and downloading a revoked share."""
    if share.state != ShareState.active:
        raise AppError(410, "SHARE_NOT_ACTIVE", "This share is no longer active.")
    if share.expires_at is not None and share.expires_at < utc_now():
        raise AppError(410, "SHARE_EXPIRED", "This share has expired.")


def is_authorized_to_download(db: Session, *, user: User, share: Share) -> bool:
    """Who may see/download a share:
    - admin: any share; creator: their own.
    - inbound (client → company): every employee/admin (the company), plus any
      client who shares ≥1 group with the creator (group-peer visibility).
    - outbound (staff → client/group): direct recipient or member of a recipient
      group.
    """
    if user.role == UserRole.admin:
        return True
    if share.created_by_id == user.id:
        return True
    if share.kind == ShareKind.inbound:
        if user.role == UserRole.employee:
            return True
        return user.role == UserRole.client and _shares_group(
            db, user.id, share.created_by_id
        )
    user_group_ids = _user_group_ids(db, user.id)
    rec = (
        db.query(ShareRecipient)
        .filter(ShareRecipient.share_id == share.id)
        .filter(
            or_(
                ShareRecipient.recipient_user_id == user.id,
                ShareRecipient.recipient_group_id.in_(user_group_ids)
                if user_group_ids
                else False,
            )
        )
        .first()
    )
    return rec is not None


def is_authorized_to_view(db: Session, *, user: User, share: Share) -> bool:
    """Who may open a share's detail (metadata only). Same as download for
    active/terminal shares, plus approvers may view a PENDING share to decide.
    A recipient can't see a pending or rejected share they were never granted."""
    if user.role == UserRole.admin or share.created_by_id == user.id:
        return True
    from . import share_approval as approval_svc
    if share.state == ShareState.pending_approval and approval_svc.can_approve(
        db, user
    ):
        return True
    if share.state in (ShareState.pending_approval, ShareState.rejected):
        return False
    return is_authorized_to_download(db, user=user, share=share)


def assert_share_file_access(db: Session, *, user: User, share: Share) -> None:
    """Gate access to a share's file BYTES (download / preview / zip). An
    approver reviewing a pending share is allowed when content review is on;
    otherwise normal authorization + the active-lifecycle gate apply (so a
    recipient still can't fetch a pending/rejected share's bytes)."""
    from . import share_approval as approval_svc
    if approval_svc.can_review_pending(db, user, share):
        return
    if not is_authorized_to_download(db, user=user, share=share):
        raise AppError(403, "FORBIDDEN", "You don't have access to this file.")
    assert_share_downloadable(share)


VALID_SORT_COLUMNS = {
    "subject",
    "state",
    "file_count",
    "total_size",
    "expires_at",
    "created_at",
}


def list_shares_for_user(
    db: Session,
    *,
    user: User,
    box: str = "outbox",
    q: str = "",
    states: list[str] | None = None,
    recipient_user_id: int | None = None,
    recipient_group_id: int | None = None,
    sender_user_id: int | None = None,
    via_group_id: int | None = None,
    sort: str = "created_at",
    direction: str = "desc",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Share], int]:
    """Return (rows, total) for a paginated, filtered, sorted query.

    `box`: 'outbox' (sender = user) or 'inbox' (user is recipient).
    Filters apply after the base box query; sorts are validated against
    `VALID_SORT_COLUMNS` and silently coerced to the default if invalid.
    """
    # Eager-load files to avoid N+1 - the router serializer iterates
    # `s.files` for file_count, total_size, and effective_subject. With
    # 50 shares per page, lazy-load would fire 50 extra queries per
    # list call.
    if box == "outbox":
        base = db.query(Share).options(joinedload(Share.files)).filter(
            Share.created_by_id == user.id
        )
        # Correlated EXISTS per filter (not a join): two joins on the same
        # ShareRecipient collided into a 500 when BOTH filters were passed, and a
        # join also row-fans-out a share with multiple matching recipients. .any()
        # is unambiguous and duplicate-free, and both filters compose (AND).
        if recipient_user_id is not None:
            base = base.filter(
                Share.recipients.any(ShareRecipient.recipient_user_id == recipient_user_id)
            )
        if recipient_group_id is not None:
            base = base.filter(
                Share.recipients.any(ShareRecipient.recipient_group_id == recipient_group_id)
            )
    elif box == "inbox":
        user_group_ids = _user_group_ids(db, user.id)
        # (a) Outbound shares addressed to me - direct, or via a group I'm in.
        recipient_match = ShareRecipient.recipient_user_id == user.id
        if user_group_ids:
            recipient_match = or_(
                recipient_match,
                ShareRecipient.recipient_group_id.in_(user_group_ids),
            )
        # (b) Inbound (client → company) shares. Staff (employee/admin) see all;
        # a client sees submissions from group-peers (creators sharing ≥1 group),
        # never their own (those are their outbox). Inbound shares have no
        # recipient rows, so this is matched on kind/creator, not the join.
        inbound_match = None
        if user.role in (UserRole.admin, UserRole.employee):
            inbound_match = Share.kind == ShareKind.inbound
        elif user_group_ids:
            peer_ids = [
                uid
                for (uid,) in (
                    db.query(GroupMember.user_id)
                    .filter(
                        GroupMember.group_id.in_(user_group_ids),
                        GroupMember.user_id != user.id,
                    )
                    .distinct()
                    .all()
                )
            ]
            if peer_ids:
                inbound_match = and_(
                    Share.kind == ShareKind.inbound,
                    Share.created_by_id.in_(peer_ids),
                )
        visibility = (
            recipient_match
            if inbound_match is None
            else or_(recipient_match, inbound_match)
        )
        base = (
            db.query(Share)
            .options(joinedload(Share.files))
            .outerjoin(ShareRecipient, ShareRecipient.share_id == Share.id)
            .filter(visibility)
        )
        if sender_user_id is not None:
            base = base.filter(Share.created_by_id == sender_user_id)
        if via_group_id is not None:
            # Only allow filtering by groups the user is a member of.
            if via_group_id not in user_group_ids:
                base = base.filter(False)
            else:
                base = base.filter(
                    ShareRecipient.recipient_group_id == via_group_id
                )
        base = base.distinct()
        # Recipients never see a share that hasn't been approved (or was
        # rejected) - those aren't 'received'. The sender's outbox shows them;
        # approvers use the dedicated approval queue.
        base = base.filter(
            Share.state.notin_(
                [ShareState.pending_approval, ShareState.rejected]
            )
        )
    else:
        raise AppError(400, "INVALID_BOX", "box must be 'outbox' or 'inbox'.")

    if q:
        # Escape LIKE wildcards so a literal % or _ in the query matches itself
        # rather than acting as "any chars" / "any char" (audit L4).
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        base = base.filter(Share.subject.ilike(f"%{esc}%", escape="\\"))

    if states:
        valid = {s.value for s in ShareState}
        accepted = [s for s in states if s in valid]
        if accepted:
            base = base.filter(Share.state.in_(accepted))

    total = base.count()

    # Sort. Most columns map directly; file_count and total_size are
    # computed downstream so we sort by created_at as a fallback for
    # them (the SPA can re-sort the page client-side if it cares).
    sort_col = sort if sort in VALID_SORT_COLUMNS else "created_at"
    direction = direction if direction in ("asc", "desc") else "desc"

    column_map = {
        "subject": Share.subject,
        "state": Share.state,
        "expires_at": Share.expires_at,
        "created_at": Share.created_at,
    }
    sort_target = column_map.get(sort_col, Share.created_at)
    order = sort_target.asc() if direction == "asc" else sort_target.desc()
    # NULL expires_at = "never". MariaDB sorts NULL first by default
    # (ASC) or last (DESC). For user-facing list display, "Never" should
    # consistently appear AFTER the dated rows regardless of direction -
    # so prepend an `IS NULL` ordering hint that pushes NULLs to the end.
    base = base.order_by(Share.expires_at.is_(None).asc(), order) if sort_col == "expires_at" else base.order_by(order)

    rows = (
        base.offset(max(0, (page - 1) * page_size)).limit(page_size).all()
    )
    return rows, total


def revoke_share(db: Session, *, user: User, share: Share, request=None) -> None:
    if share.created_by_id != user.id and user.role != UserRole.admin:
        raise AppError(403, "FORBIDDEN", "You cannot revoke this share.")
    if share.state == ShareState.deleted:
        raise AppError(409, "ALREADY_DELETED", "Share already deleted.")
    share.state = ShareState.revoked
    # Stamp the terminal transition so the orphan-reclaim cron can age its
    # grace window. Bytes are kept (soft revoke); the cron frees them later.
    share.terminated_at = utc_now()
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.share_revoked,
        actor_user_id=user.id,
        target_type="share",
        target_id=share.id,
        request=request,
    )


# ---------------------------------------------------------------------------
# Share-approval workflow (v1.24.0)
# ---------------------------------------------------------------------------


def _assert_can_decide(db: Session, user: User, share: Share) -> None:
    """Common guard for approve/reject: an approver, not the creator (no
    self-approval ever), on a share that's still pending."""
    from . import share_approval as approval_svc
    if not approval_svc.can_approve(db, user):
        raise AppError(403, "FORBIDDEN", "You're not allowed to decide on shares.")
    if share.created_by_id == user.id:
        raise AppError(403, "SELF_APPROVAL", "You can't decide on your own share.")
    if share.state != ShareState.pending_approval:
        raise AppError(409, "SHARE_NOT_PENDING", "This share isn't awaiting approval.")


def approve_share(db: Session, *, user: User, share: Share, request=None) -> Share:
    """Approver flips a pending share live, fires the deferred recipient
    notifications, and tells the creator. Atomic flip guards double-approve."""
    from ..models.notification import NotificationCategory

    _assert_can_decide(db, user, share)
    if share.expires_at is not None and share.expires_at < utc_now():
        raise AppError(
            409,
            "SHARE_EXPIRY_PASSED",
            "This share's expiry has already passed - ask the sender to resubmit with a later expiry.",
        )
    now = utc_now()
    result = db.execute(
        update(Share)
        .where(Share.id == share.id, Share.state == ShareState.pending_approval)
        .values(
            state=ShareState.active,
            approval_decided_by_id=user.id,
            approval_decided_at=now,
            rejection_reason=None,
        )
    )
    if result.rowcount == 0:
        raise AppError(409, "SHARE_NOT_PENDING", "This share isn't awaiting approval.")
    db.flush()
    db.refresh(share)
    record_audit_event(
        db,
        event_type=AuditEventType.share_approved,
        actor_user_id=user.id,
        target_type="share",
        target_id=share.id,
        metadata={"creator_id": share.created_by_id},
        request=request,
    )
    notify = share.notify_on_activation if share.notify_on_activation is not None else True
    _dispatch_share_created(db, share, notify_recipients=notify)
    _notify_share_decision(
        db, share, category=NotificationCategory.share_approved, reason=None
    )
    return share


def reject_share(
    db: Session, *, user: User, share: Share, reason: str | None = None, request=None
) -> Share:
    """Approver rejects a pending share → `rejected`. Bytes are kept (the owner
    can resubmit); the creator is notified with the reason."""
    from ..models.notification import NotificationCategory

    _assert_can_decide(db, user, share)
    reason = (reason or "").strip()[:1000] or None
    now = utc_now()
    result = db.execute(
        update(Share)
        .where(Share.id == share.id, Share.state == ShareState.pending_approval)
        .values(
            state=ShareState.rejected,
            approval_decided_by_id=user.id,
            approval_decided_at=now,
            rejection_reason=reason,
        )
    )
    if result.rowcount == 0:
        raise AppError(409, "SHARE_NOT_PENDING", "This share isn't awaiting approval.")
    db.flush()
    db.refresh(share)
    record_audit_event(
        db,
        event_type=AuditEventType.share_rejected,
        actor_user_id=user.id,
        target_type="share",
        target_id=share.id,
        metadata={"creator_id": share.created_by_id, "has_reason": reason is not None},
        request=request,
    )
    _notify_share_decision(
        db, share, category=NotificationCategory.share_rejected, reason=reason
    )
    return share


def resubmit_share(db: Session, *, user: User, share: Share, request=None) -> Share:
    """Owner re-queues a rejected share for approval (as-is). Clears the prior
    decision + re-notifies approvers."""
    if share.created_by_id != user.id:
        raise AppError(403, "FORBIDDEN", "Only the share owner can resubmit it.")
    if share.state != ShareState.rejected:
        raise AppError(
            409, "SHARE_NOT_REJECTED", "Only a rejected share can be resubmitted."
        )
    result = db.execute(
        update(Share)
        .where(Share.id == share.id, Share.state == ShareState.rejected)
        .values(
            state=ShareState.pending_approval,
            approval_decided_by_id=None,
            approval_decided_at=None,
            rejection_reason=None,
        )
    )
    if result.rowcount == 0:
        raise AppError(
            409, "SHARE_NOT_REJECTED", "Only a rejected share can be resubmitted."
        )
    db.flush()
    db.refresh(share)
    record_audit_event(
        db,
        event_type=AuditEventType.share_resubmitted,
        actor_user_id=user.id,
        target_type="share",
        target_id=share.id,
        request=request,
    )
    _notify_approvers_pending(db, share)
    return share


def list_pending_approvals(
    db: Session, *, user: User, page: int = 1, page_size: int = 50
) -> tuple[list[Share], int]:
    """Shares awaiting approval that `user` may act on (approver, not own),
    oldest first. Empty when the user can't approve."""
    from . import share_approval as approval_svc
    if not approval_svc.can_approve(db, user):
        return [], 0
    base = (
        db.query(Share)
        .options(joinedload(Share.files))
        .filter(
            Share.state == ShareState.pending_approval,
            Share.created_by_id != user.id,
        )
    )
    total = base.count()
    rows = (
        base.order_by(Share.created_at.asc())
        .offset(max(0, (page - 1) * page_size))
        .limit(page_size)
        .all()
    )
    return rows, total


# ---------------------------------------------------------------------------
# Editable expiry + Expire-now (post-Phase 10)
# ---------------------------------------------------------------------------


def try_decrement_share_counter(db: Session, *, share: Share) -> bool:
    """Atomically decrement `downloads_remaining` for a share. Mirrors
    `services/public_link.py::decrement_counter`. Returns True on
    success, False when the budget is exhausted (caller raises 410).

    Caller is responsible for the `share.download_limit is not None`
    pre-check - this function is a no-op-on-call for unlimited shares
    because the WHERE clause filters them out.
    """
    from sqlalchemy import update as _update

    stmt = (
        _update(Share)
        .where(Share.id == share.id, Share.downloads_remaining > 0)
        .values(downloads_remaining=Share.downloads_remaining - 1)
    )
    result = db.execute(stmt)
    db.flush()
    if result.rowcount == 0:
        return False
    db.refresh(share)
    return True


def update_share_limit(
    db: Session,
    *,
    user: User,
    share: Share,
    new_limit: int | None,
    clear: bool,
    request=None,
) -> Share:
    """Owner-or-admin changes the per-share download budget.

    Semantics:
    - `clear=True`  → both download_limit and downloads_remaining → NULL (unlimited).
    - `new_limit=N` → preserves the used count (used = old_limit - old_remaining,
      or 0 if previously unlimited); new_remaining = max(0, new_limit - used).
      So raising the limit grows the remaining by the delta; lowering it below
      already-used clamps to 0 (sender sees "5 of 3", informative not destructive).
    - `new_limit=None` AND `clear=False` → no-op (PATCH "no change").

    Refuses non-active shares (would mean editing a budget on a revoked
    or expired share with no downloads coming).
    """
    if share.created_by_id != user.id and user.role != UserRole.admin:
        raise AppError(403, "FORBIDDEN", "You cannot edit this share.")
    if share.state != ShareState.active:
        raise AppError(
            409,
            "SHARE_NOT_ACTIVE",
            "Only active shares can have their download limit changed.",
        )

    old_limit = share.download_limit
    old_remaining = share.downloads_remaining

    if clear:
        if old_limit is None:
            return share  # already unlimited; no-op + no audit
        share.download_limit = None
        share.downloads_remaining = None
    elif new_limit is None:
        return share  # no-change
    else:
        used = (old_limit - old_remaining) if old_limit is not None and old_remaining is not None else 0
        share.download_limit = new_limit
        share.downloads_remaining = max(0, new_limit - used)

    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.share_limit_updated,
        actor_user_id=user.id,
        target_type="share",
        target_id=share.id,
        metadata={
            "old_limit": old_limit,
            "old_remaining": old_remaining,
            "new_limit": share.download_limit,
            "new_remaining": share.downloads_remaining,
            "cleared": clear,
        },
        request=request,
    )
    return share


def update_share_expiry(
    db: Session,
    *,
    user: User,
    share: Share,
    new_expires_at: datetime | None,
    request=None,
) -> Share:
    """Owner-or-admin extends, shortens, or clears an active share's expiry.
    Refuses non-active shares (bytes might be gone) or past timestamps
    (use `expire_share_now` for that). new_expires_at=None clears the
    field - the share becomes never-expire (v1.1.4).
    """
    if share.created_by_id != user.id and user.role != UserRole.admin:
        raise AppError(403, "FORBIDDEN", "You cannot edit this share.")
    if share.state != ShareState.active:
        raise AppError(
            409,
            "SHARE_NOT_ACTIVE",
            "Only active shares can have their expiry changed.",
        )
    if new_expires_at is not None:
        # Normalise to naive UTC (matches DB convention).
        if new_expires_at.tzinfo is not None:
            new_expires_at = new_expires_at.astimezone(timezone.utc).replace(tzinfo=None)
        if new_expires_at < utc_now():
            raise AppError(
                400,
                "INVALID_EXPIRY",
                "New expiry must be in the future. Use 'expire now' for immediate expiry.",
            )
    old = share.expires_at
    share.expires_at = new_expires_at
    # Clearing the expiry also resets the 24h-warning idempotency
    # marker so that a future re-narrowing of the window can fire a
    # fresh warning.
    if new_expires_at is None:
        share.expiring_notified_at = None
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.share_expiry_updated,
        actor_user_id=user.id,
        target_type="share",
        target_id=share.id,
        metadata={
            "old_expires_at": old.isoformat() if old else None,
            "new_expires_at": new_expires_at.isoformat() if new_expires_at else None,
        },
        request=request,
    )
    return share


def expire_share_now(
    db: Session, *, user: User, share: Share, request=None
) -> Share:
    """Owner-or-admin expires a share immediately. Hard-deletes every
    file and transitions state to `expired`. Re-uses the same helper
    the cron uses (`services/file.py::delete_file_for_expiry`).

    Concurrent expire-now calls on the same share are guarded by an
    atomic conditional UPDATE - only the first wins the state flip; the
    others see 409 SHARE_NOT_ACTIVE."""
    if share.created_by_id != user.id and user.role != UserRole.admin:
        raise AppError(403, "FORBIDDEN", "You cannot expire this share.")

    now = utc_now()
    result = db.execute(
        update(Share)
        .where(Share.id == share.id, Share.state == ShareState.active)
        .values(state=ShareState.expired, expires_at=now)
    )
    if result.rowcount == 0:
        raise AppError(
            409, "SHARE_NOT_ACTIVE", "Only active shares can be expired."
        )
    db.flush()
    db.refresh(share)

    # Lazy import - services.file imports services.share elsewhere; keep
    # the dependency direction loose.
    from .file import delete_file_for_expiry

    file_count = 0
    failed_files: list[str] = []
    for f in list(share.files):
        try:
            delete_file_for_expiry(db, file=f)
            file_count += 1
        except OSError as e:
            logger.error(
                "expire_share_now: delete failed file=%s share=%s: %s",
                f.id, share.id, e,
            )
            failed_files.append(f.id)

    metadata: dict = {"via": "owner_action", "file_count": file_count}
    if failed_files:
        metadata["failed_files"] = failed_files
    record_audit_event(
        db,
        event_type=AuditEventType.share_expired,
        actor_user_id=user.id,
        target_type="share",
        target_id=share.id,
        metadata=metadata,
        request=request,
    )
    return share


def register_files_added(
    db: Session,
    *,
    user: User,
    share: Share,
    file_ids: list[str],
    notify: bool,
    request=None,
) -> Share:
    """Owner finished adding files to an already-active share. The files were
    already attached by the upload pipeline (which gates on owner + active);
    this is the batch-complete signal - it records a share-level audit row and,
    when ``notify``, fans a ``share_files_added`` notification out to the same
    recipient set ``create_share`` resolves (sourced from the share's current
    recipients). Caller commits."""
    if share.created_by_id != user.id:
        raise AppError(403, "FORBIDDEN", "Only the share owner can add files.")
    if share.state != ShareState.active:
        raise AppError(
            409, "SHARE_NOT_ACTIVE", "Only active shares can receive files."
        )

    from ..models.file import File

    valid_ids = (
        [
            fid
            for (fid,) in db.query(File.id)
            .filter(File.share_id == share.id, File.id.in_(file_ids))
            .all()
        ]
        if file_ids
        else []
    )
    added_count = len(valid_ids)

    record_audit_event(
        db,
        event_type=AuditEventType.share_files_added,
        actor_user_id=user.id,
        target_type="share",
        target_id=share.id,
        metadata={"count": added_count, "file_ids": valid_ids, "notified": bool(notify)},
        request=request,
    )

    if notify and added_count > 0:
        from ..models.notification import NotificationCategory
        from . import notification as notif_svc
        from . import site as site_svc

        # Same recipient resolution as create_share's notify block, but
        # sourced from the share's existing recipient rows.
        notify_user_ids: set[int] = set()
        if share.kind == ShareKind.inbound:
            staff_rows = (
                db.query(User.id)
                .filter(
                    User.role.in_([UserRole.employee, UserRole.admin]),
                    User.is_disabled.is_(False),
                )
                .all()
            )
            notify_user_ids = {uid for (uid,) in staff_rows}
        else:
            direct_ids = [
                r.recipient_user_id
                for r in share.recipients
                if r.recipient_user_id is not None
            ]
            group_ids = [
                r.recipient_group_id
                for r in share.recipients
                if r.recipient_group_id is not None
            ]
            notify_user_ids = set(direct_ids)
            if group_ids:
                member_rows = (
                    db.query(GroupMember.user_id)
                    .join(User, User.id == GroupMember.user_id)
                    .filter(
                        GroupMember.group_id.in_(group_ids),
                        User.is_disabled.is_(False),
                    )
                    .all()
                )
                for (uid,) in member_rows:
                    notify_user_ids.add(uid)
        notify_user_ids.discard(user.id)

        if notify_user_ids:
            base_url = site_svc.get_site_url(db)
            payload_base = {
                "sender_name": user.display_name,
                "subject": share.subject,
                "added_count": added_count,
                "share_url": f"{base_url}/share/{share.id}",
            }
            recipients = db.query(User).filter(User.id.in_(notify_user_ids)).all()
            for u in recipients:
                payload = dict(payload_base)
                payload["recipient_name"] = u.display_name
                notif_svc.dispatch(
                    db,
                    user=u,
                    category=NotificationCategory.share_files_added,
                    payload=payload,
                    link_url=payload["share_url"],
                    email_to=u.email,
                )

    db.flush()
    return share


def invalidate_all_active_shares(
    db: Session, *, actor: User | None = None, request=None
) -> dict:
    """Expire every currently-active share and hard-delete its file bytes.

    Used by the config-restore flow: importing a configuration changes the
    world out from under any live share, so all of them are invalidated. Mirrors
    the hourly ``expire_files`` cron (flip state -> expired, delete bytes via
    ``delete_file_for_expiry``, audit ``share_expired`` per share) but with an
    admin actor and a single summary audit row.

    Irreversible (disk unlink), so the caller MUST run this in its own committed
    pass *before* the config transaction. Returns a small summary dict."""
    from sqlalchemy.orm import selectinload

    from .file import delete_file_for_expiry

    shares = (
        db.query(Share)
        .options(selectinload(Share.files))
        .filter(Share.state == ShareState.active)
        .all()
    )
    expired_shares = 0
    deleted_files = 0
    for share in shares:
        file_count = 0
        failed_files: list[str] = []
        for f in share.files:
            try:
                delete_file_for_expiry(db, file=f)
                file_count += 1
                deleted_files += 1
            except OSError as e:
                logger.error(
                    "invalidate_all_active_shares: delete failed file=%s share=%s: %s",
                    f.id, share.id, e,
                )
                failed_files.append(f.id)
        share.state = ShareState.expired
        share.expires_at = utc_now()
        metadata: dict = {"file_count": file_count, "via": "config_restore"}
        if failed_files:
            metadata["failed_files"] = failed_files
        record_audit_event(
            db,
            event_type=AuditEventType.share_expired,
            actor_user_id=actor.id if actor else None,
            target_type="share",
            target_id=share.id,
            metadata=metadata,
            request=request,
        )
        expired_shares += 1
    db.flush()
    logger.info(
        "invalidate_all_active_shares: expired %d shares, deleted %d files",
        expired_shares, deleted_files,
    )
    return {"expired_shares": expired_shares, "deleted_files": deleted_files}
