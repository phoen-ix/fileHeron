"""Share lifecycle.

Phase 4 model: a share has N user-recipients and M group-recipients.
Inbox resolution is dynamic — group membership at query time decides
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
from .audit import record_audit_event

logger = logging.getLogger("fileheron.share")


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


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
    # client at all, e.g. another employee — also allowed). Groups must be
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
    # Normalise to naive UTC before comparing or storing — mirrors the
    # convention every other write site follows. See update_expiry()
    # below for the same guard at the PATCH boundary.
    if expires_at is not None:
        if expires_at.tzinfo is not None:
            expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
        if expires_at < _utcnow():
            raise AppError(400, "EXPIRY_IN_PAST", "Expiry must be in the future.")

    # Inbound (client → the company): the client never picks recipients. The
    # audience — every employee/admin, plus the creator's group-peers — is
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
        # attached — the link is the access path).
        if not user_ids and not group_ids and not allow_no_recipients:
            raise AppError(
                400, "NO_RECIPIENTS", "At least one user or group recipient is required."
            )
        for uid in user_ids:
            u = db.query(User).filter(User.id == uid).one_or_none()
            if u is None or u.is_disabled:
                raise AppError(
                    404, "RECIPIENT_NOT_FOUND", f"Recipient user {uid} is not available."
                )
            if u.id == created_by.id:
                raise AppError(400, "SELF_SHARE", "Cannot share with yourself.")
            users.append(u)
        for gid in group_ids:
            g = db.query(Group).filter(Group.id == gid).one_or_none()
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

    record_audit_event(
        db,
        event_type=AuditEventType.share_created,
        actor_user_id=created_by.id,
        target_type="share",
        target_id=share.id,
        metadata={
            "kind": kind.value,
            "recipient_user_ids": [u.id for u in users],
            "recipient_group_ids": [g.id for g in groups],
        },
        request=request,
    )

    # Build the notify set. Inbound (client → company): every non-disabled
    # employee + admin (the company), regardless of the per-share flag —
    # group-peers are intentionally NOT notified, they see it in their inbox.
    # Outbound: the per-share notify flag gates direct users UNION active
    # group members. Each recipient still honours their own per-user
    # notification preference at dispatch time. Sender always excluded.
    from ..models.notification import NotificationCategory
    from . import notification as notif_svc
    from . import settings as settings_svc
    from . import site as site_svc

    notify_user_ids: set[int] = set()
    if kind == ShareKind.inbound:
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
        if notify_recipients is None:
            notify_recipients = settings_svc.get_bool(
                db,
                settings_svc.Keys.SHARE_NOTIFY_RECIPIENTS_DEFAULT,
                default=True,
            )
        if notify_recipients:
            notify_user_ids = {u.id for u in users}
            if groups:
                member_rows = (
                    db.query(GroupMember.user_id)
                    .join(User, User.id == GroupMember.user_id)
                    .filter(
                        GroupMember.group_id.in_([g.id for g in groups]),
                        User.is_disabled.is_(False),
                    )
                    .all()
                )
                for (uid,) in member_rows:
                    notify_user_ids.add(uid)
    notify_user_ids.discard(created_by.id)

    if notify_user_ids:
        base_url = site_svc.get_site_url(db)
        payload_base = {
            "sender_name": created_by.display_name,
            "subject": share.subject,
            "message": share.message,
            "expires_at": share.expires_at,
            "file_count": 0,  # files added after the share row; stays 0 here
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
    if share.expires_at is not None and share.expires_at < _utcnow():
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
    # Eager-load files to avoid N+1 — the router serializer iterates
    # `s.files` for file_count, total_size, and effective_subject. With
    # 50 shares per page, lazy-load would fire 50 extra queries per
    # list call.
    if box == "outbox":
        base = db.query(Share).options(joinedload(Share.files)).filter(
            Share.created_by_id == user.id
        )
        if recipient_user_id is not None:
            base = base.join(ShareRecipient, ShareRecipient.share_id == Share.id).filter(
                ShareRecipient.recipient_user_id == recipient_user_id
            )
        if recipient_group_id is not None:
            base = base.join(ShareRecipient, ShareRecipient.share_id == Share.id).filter(
                ShareRecipient.recipient_group_id == recipient_group_id
            )
    elif box == "inbox":
        user_group_ids = _user_group_ids(db, user.id)
        # (a) Outbound shares addressed to me — direct, or via a group I'm in.
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
    else:
        raise AppError(400, "INVALID_BOX", "box must be 'outbox' or 'inbox'.")

    if q:
        like = f"%{q}%"
        base = base.filter(Share.subject.ilike(like))

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
    # consistently appear AFTER the dated rows regardless of direction —
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
    share.terminated_at = _utcnow()
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
# Editable expiry + Expire-now (post-Phase 10)
# ---------------------------------------------------------------------------


def try_decrement_share_counter(db: Session, *, share: Share) -> bool:
    """Atomically decrement `downloads_remaining` for a share. Mirrors
    `services/public_link.py::decrement_counter`. Returns True on
    success, False when the budget is exhausted (caller raises 410).

    Caller is responsible for the `share.download_limit is not None`
    pre-check — this function is a no-op-on-call for unlimited shares
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
    field — the share becomes never-expire (v1.1.4).
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
        if new_expires_at < _utcnow():
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
    atomic conditional UPDATE — only the first wins the state flip; the
    others see 409 SHARE_NOT_ACTIVE."""
    if share.created_by_id != user.id and user.role != UserRole.admin:
        raise AppError(403, "FORBIDDEN", "You cannot expire this share.")

    now = _utcnow()
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

    # Lazy import — services.file imports services.share elsewhere; keep
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
