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
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, or_, update
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

if TYPE_CHECKING:
    from .file import PurgeEntry

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

    # Cleared either way: the announcement for this share has now been made (or
    # has been established to go to nobody), and `notify_on_activation` is what
    # `announce_if_ready` reads to decide whether one is still owed.
    share.notify_on_activation = None
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
        # `approval_was_required` outlives the decision: it is what tells a later
        # upload into the (by then active) share that it needs its own review.
        share.state = ShareState.pending_approval
        share.approval_was_required = True
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
    #
    # Files attach at UPLOAD time (file_svc.create_pending sets files.share_id),
    # and every client creates the share first: the SPA posts the form, then
    # starts Uppy; the desktop client does the same. So at this point the share
    # is empty, and announcing here told every recipient "shared 0 files with
    # you" and linked them to a share page reading "Files (0)" - in mail, in the
    # bell, in both locales, for every share this product has ever sent
    # (audit #2). Freeze the choice the same way the approval path does and let
    # `announce_if_ready` fire it once the uploads land.
    if _has_landed_file(db, share):
        _dispatch_share_created(db, share, notify_recipients=resolved_notify)
    else:
        share.notify_on_activation = resolved_notify
        db.flush()
    return share


def _still_uploading(db: Session, share: Share) -> bool:
    from ..models.file import File, FileState

    return (
        db.query(File.id)
        .filter(File.share_id == share.id, File.state == FileState.uploading)
        .first()
        is not None
    )


def _has_landed_file(db: Session, share: Share) -> bool:
    from ..models.file import File, FileState

    return (
        db.query(File.id)
        .filter(
            File.share_id == share.id,
            File.state.notin_((FileState.uploading, FileState.deleted)),
        )
        .first()
        is not None
    )


# How long a share must have been quiet before the fallback sweep decides its
# batch is finished. Every shipped client uploads SEQUENTIALLY, so "nothing is
# in `uploading` right now" is true in the gap between file 1 finishing and file
# 2 starting - announcing there would have said "1 file" for a three-file share,
# which is the same defect one size smaller (audit #2 cross-check).
ANNOUNCE_QUIET_SECONDS = 90


def announce_if_ready(db: Session, share_id: str, *, require_quiet: bool = False) -> bool:
    """Send the deferred `share_created` announcement once the share's uploads
    have landed. Returns True if this call is the one that sent it.

    Two callers, two meanings:

    - The owner's explicit batch-complete signal (`files-added`) knows the batch
      is over, so it calls with `require_quiet=False` and announces at once.
    - The fallback sweep, for clients that send no such signal, calls with
      `require_quiet=True`: nothing uploading AND nothing new for
      `ANNOUNCE_QUIET_SECONDS`. Without that a sequential upload announces after
      the first file.

    Idempotent and concurrency-safe: the claim is a conditional UPDATE clearing
    `notify_on_activation`, so of two callers racing exactly one announces.
    """
    from datetime import timedelta

    from ..models.file import File

    share = db.query(Share).filter(Share.id == share_id).one_or_none()
    if share is None or share.state != ShareState.active:
        return False
    if share.notify_on_activation is None:
        return False
    if _still_uploading(db, share) or not _has_landed_file(db, share):
        return False
    if require_quiet:
        newest = (
            db.query(func.max(File.created_at))
            .filter(File.share_id == share.id)
            .scalar()
        )
        if newest is not None and newest > utc_now() - timedelta(
            seconds=ANNOUNCE_QUIET_SECONDS
        ):
            return False

    notify = bool(share.notify_on_activation)
    claimed = db.execute(
        update(Share)
        .where(Share.id == share.id, Share.notify_on_activation.isnot(None))
        .values(notify_on_activation=None)
    ).rowcount
    if not claimed:
        return False
    db.flush()
    db.refresh(share)
    _dispatch_share_created(db, share, notify_recipients=notify)
    return True


def has_recent_archive_download(
    db: Session, *, share_id: str, user_id: int, etag: str, within_hours: int
) -> bool:
    """Whether this user already paid for THIS EXACT archive recently.

    The durable half of the bulk-ZIP resume evidence. The Redis payment mark is
    the fast path and vanishes on a restart - which the v2.5.0 host step
    performs, and which any host reboot does - after which a legitimate resume
    was re-charged and, on a spent budget, answered 410 (audit #2 cross-check).
    The desktop client can pause a download and resume it the next day, so this
    is measured in hours.

    Keyed on the ETAG, so it is evidence about an ARCHIVE. Its predecessor
    accepted a `download_log` row for one member as evidence that an archive
    transfer was in progress, which is what made the bypass possible.
    """
    from datetime import timedelta

    from ..models.audit_log import AuditEventType, AuditLog

    cutoff = utc_now() - timedelta(hours=max(1, within_hours))
    rows = (
        db.query(AuditLog.extra)
        .filter(
            AuditLog.event_type == AuditEventType.share_downloaded.value,
            AuditLog.target_id == share_id,
            AuditLog.actor_user_id == user_id,
            AuditLog.created_at >= cutoff,
        )
        .all()
    )
    return any(isinstance(e, dict) and e.get("etag") == etag for (e,) in rows)


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
    # An ACTIVE share carrying files that need this approver's decision. The
    # approver is notified about it and must echo a `content_fingerprint` read
    # off this very payload, but was refused here - so they could approve blind
    # over the API and never see what they were approving.
    if approval_svc.can_review_this_share(db, user, share):
        return True
    return is_authorized_to_download(db, user=user, share=share)


def assert_share_file_access(
    db: Session, *, user: User, share: Share, file=None
) -> None:
    """Gate access to a share's file BYTES (download / preview / zip). An
    approver reviewing a pending share is allowed when content review is on;
    otherwise normal authorization + the active-lifecycle gate apply (so a
    recipient still can't fetch a pending/rejected share's bytes).

    Pass ``file`` on any route that serves ONE file: a file added to an
    already-approved share carries its own `pending_review` mark, and the
    share-level checks above cannot see it. The bulk-ZIP routes pass no file
    because `file_svc.downloadable_files` filters the member list instead."""
    from . import share_approval as approval_svc
    if approval_svc.can_review_pending(db, user, share):
        return
    # Same admission as `is_authorized_to_view`. `can_review_pending` is False
    # here by construction (the share is active, not pending), so without this
    # the download check below refused the approver and
    # `assert_file_approved`'s `can_review_added_files` branch - written for
    # exactly this case - was unreachable.
    reviewing = approval_svc.can_review_this_share(db, user, share)
    if not reviewing and not is_authorized_to_download(db, user=user, share=share):
        raise AppError(403, "FORBIDDEN", "You don't have access to this file.")
    assert_share_downloadable(share)
    if file is not None:
        assert_file_approved(db, user=user, share=share, file=file)


def assert_file_approved(db: Session, *, user: User, share: Share, file) -> None:
    """Refuse a file that is still awaiting its own four-eyes decision.

    The owner keeps access to what they uploaded (they are assembling the batch
    and must be able to verify it), and approvers may fetch it when content
    review is on - that is the whole point of the queue. Everyone else,
    recipients included, waits for the decision."""
    from ..models.file import FileApprovalState
    from . import share_approval as approval_svc

    if file.approval_state == FileApprovalState.approved:
        return
    if user.id == share.created_by_id:
        return
    if approval_svc.can_review_added_files(db, user):
        return
    raise AppError(
        409,
        "FILE_PENDING_APPROVAL",
        "This file was added after the share was approved and is awaiting review.",
    )


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


def approve_share(
    db: Session,
    *,
    user: User,
    share: Share,
    request=None,
    expect_fingerprint: str,
) -> Share:
    """Approver flips a pending share live, fires the deferred recipient
    notifications, and tells the creator. Atomic flip guards double-approve.

    ``expect_fingerprint`` is the content digest the approver's review screen
    rendered, and it is REQUIRED. The owner may keep adding files to a pending
    share, so approving without it signs off on whatever the share happens to
    contain at the instant the button is clicked. It was optional for one
    release so API-token clients that predated it kept working - but a control
    a caller may simply omit is not a control, and the party that benefits from
    omitting it is the one being reviewed. Stale digests are refused 409."""
    from ..models.notification import NotificationCategory
    from . import share_approval as approval_svc

    _assert_can_decide(db, user, share)
    # Nothing may be mid-flight: `create_pending` writes a row before any byte
    # lands, so approving now would sign off on a filename and a promised size.
    if _still_uploading(db, share):
        raise AppError(
            409,
            "FILES_NOT_READY",
            "This share is still receiving files - review it once they've all landed.",
        )
    current = approval_svc.content_fingerprint(db, share)
    if not secrets.compare_digest(expect_fingerprint, current):
        raise AppError(
            409,
            "CONTENT_CHANGED",
            "This share changed since you opened it - review it again before approving.",
            details={"content_fingerprint": current},
        )
    if share.expires_at is not None and share.expires_at < utc_now():
        raise AppError(
            409,
            "SHARE_EXPIRY_PASSED",
            "This share's expiry has already passed - ask the sender to extend it, then approve.",
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
    # Announce only if the files are actually there. An approver can decide
    # while the owner is still uploading (add-files is allowed on a pending
    # share, deliberately), and dispatching here announced whatever count
    # existed at that instant - "0 files" for an approval granted before the
    # first upload landed, with nothing to correct it afterwards (audit #2
    # cross-check). If they are not there yet, the frozen flag stays set and
    # the batch signal or the announce sweep does it.
    if _has_landed_file(db, share) and not _still_uploading(db, share):
        notify = (
            share.notify_on_activation if share.notify_on_activation is not None else True
        )
        _dispatch_share_created(db, share, notify_recipients=notify)
    elif share.notify_on_activation is None:
        # Never went through create_share's deferral (an older row): keep the
        # default so the sweep still announces it.
        share.notify_on_activation = True
        db.flush()
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
            # Stamp the terminal time so reclaim_orphaned_files can age this
            # share out. Without it a rejected share's bytes were reclaimed by
            # nothing at all: rejection deliberately keeps the files so the
            # owner can resubmit, but if they never do, the bytes sat against
            # the uploader's quota indefinitely - past the share's own expiry,
            # because an expired share is only swept while `active`
            # (audit 2026-07-30).
            terminated_at=utc_now(),
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


def _dispatch_files_added_after_approval(
    db: Session, share: Share, *, file_ids: list[str]
) -> None:
    """Files added to an approved share just became reachable - send the
    recipients the same `share_files_added` notice `register_files_added` sends
    for an ungated share. It was suppressed at upload time because the files
    were not downloadable yet."""
    if share.state != ShareState.active:
        return
    owner = share.created_by or db.query(User).get(share.created_by_id)
    if owner is None:
        return
    notify = share.notify_on_activation if share.notify_on_activation is not None else True
    if not notify:
        return
    _notify_recipients_files_added(
        db, share, actor=owner, added_count=len(file_ids)
    )


def _notify_added_files_decision(
    db: Session, share: Share, *, approved: bool, reason: str | None
) -> None:
    """Tell the owner what happened to the files they appended. Reuses the
    share-level approved/rejected categories: from the sender's point of view it
    is the same event ("your content was reviewed"), and inventing a second
    template pair for it would add two locales of copy for no new information."""
    from ..models.notification import NotificationCategory

    _notify_share_decision(
        db,
        share,
        category=(
            NotificationCategory.share_approved
            if approved
            else NotificationCategory.share_rejected
        ),
        reason=reason,
    )


def decide_added_files(
    db: Session,
    *,
    user: User,
    share: Share,
    approve: bool,
    expect_fingerprint: str,
    reason: str | None = None,
    request=None,
) -> tuple[Share, list[str | None]]:
    """Approve or reject the files added to an ALREADY-approved share.

    Returns ``(share, to_purge)``. On rejection the caller must unlink the
    returned locators AFTER committing - the ordering v2.5.0 established, so a
    commit that then fails cannot leave a `clean` row pointing at nothing.

    The share stays `active` throughout, deliberately. Reverting it to
    `pending_approval` would be destructive rather than additive:
    `assert_share_downloadable` and `public_link.assert_link_usable` are
    active-only, so every existing recipient would start getting 410 and a live
    public link would go dark because someone appended a file. Only the new
    files are gated, so the approved set keeps flowing while these wait.

    Rejection hard-deletes the added bytes: unlike a rejected share there is no
    resubmit path for a single file, and leaving them `pending_review` forever
    would hold the uploader's quota against content an approver refused.
    """
    from ..models.file import File, FileApprovalState
    from . import share_approval as approval_svc
    from .file import hard_delete

    if not approval_svc.can_approve(db, user):
        raise AppError(403, "FORBIDDEN", "You may not decide on shares.")
    if share.created_by_id == user.id:
        raise AppError(403, "SELF_APPROVAL", "You can't decide on your own share.")
    if _still_uploading(db, share):
        raise AppError(
            409,
            "FILES_NOT_READY",
            "This share is still receiving files - review it once they've all landed.",
        )
    pending = approval_svc.files_awaiting_review(db, share)
    if not pending:
        raise AppError(409, "NO_FILES_PENDING", "No files are awaiting review on this share.")

    current = approval_svc.content_fingerprint(db, share)
    if not secrets.compare_digest(expect_fingerprint, current):
        raise AppError(
            409,
            "CONTENT_CHANGED",
            "This share changed since you opened it - review it again before deciding.",
            details={"content_fingerprint": current},
        )

    now = utc_now()
    # `list[str | None]` rather than `list[str]`: `purge_locators` takes that
    # type and Python lists are invariant, so the narrower annotation would not
    # be assignable at the call site.
    to_purge: list[str | None] = []
    if approve:
        # Conditional UPDATE, mirroring approve_share's atomic flip: two
        # approvers clicking at once must not both count as the decider.
        result = db.execute(
            update(File)
            .where(
                File.share_id == share.id,
                File.approval_state == FileApprovalState.pending_review,
            )
            .values(approval_state=FileApprovalState.approved)
        )
        if result.rowcount == 0:
            raise AppError(409, "NO_FILES_PENDING", "No files are awaiting review on this share.")
        db.flush()
        record_audit_event(
            db,
            event_type=AuditEventType.share_files_approved,
            actor_user_id=user.id,
            target_type="share",
            target_id=share.id,
            metadata={"creator_id": share.created_by_id, "file_ids": pending, "decided_at": now.isoformat()},
            request=request,
        )
        _dispatch_files_added_after_approval(db, share, file_ids=pending)
    else:
        reason = (reason or "").strip()[:1000] or None
        for f in (
            db.query(File)
            .filter(
                File.share_id == share.id,
                File.approval_state == FileApprovalState.pending_review,
            )
            .all()
        ):
            locator = hard_delete(
                db,
                file=f,
                reason="approval_rejected",
                actor_user_id=user.id,
                request=request,
                purge=False,
            )
            if locator:
                to_purge.append(locator)
        db.flush()
        record_audit_event(
            db,
            event_type=AuditEventType.share_files_rejected,
            actor_user_id=user.id,
            target_type="share",
            target_id=share.id,
            metadata={"creator_id": share.created_by_id, "file_ids": pending, "has_reason": reason is not None},
            request=request,
        )
    _notify_added_files_decision(
        db, share, approved=approve, reason=None if approve else reason
    )
    return share, to_purge


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
    # Active shares with files awaiting a post-approval decision belong here
    # too. `schemas/share.py` already says so ("the approvals view should offer
    # this share even though its state is active"), but the filter was
    # state-only, so the approver was notified about a share that appeared
    # nowhere in their queue and 403'd when they followed the link.
    from ..models.file import File, FileApprovalState, FileState

    awaiting = (
        db.query(File.share_id)
        .filter(
            File.approval_state == FileApprovalState.pending_review,
            File.state != FileState.deleted,
        )
        .distinct()
    )
    base = (
        db.query(Share)
        .options(joinedload(Share.files))
        .filter(
            or_(
                Share.state == ShareState.pending_approval,
                and_(
                    Share.state == ShareState.active,
                    Share.id.in_(awaiting.scalar_subquery()),
                ),
            ),
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
    """Owner-or-admin extends, shortens, or clears the expiry of a share that is
    active or awaiting approval. Refuses terminal states (bytes might be gone)
    or past timestamps (use `expire_share_now` for that). new_expires_at=None
    clears the field - the share becomes never-expire (v1.1.4).
    """
    if share.created_by_id != user.id and user.role != UserRole.admin:
        raise AppError(403, "FORBIDDEN", "You cannot edit this share.")
    # `pending_approval` belongs here. approve_share refuses a share whose
    # expiry has already passed and tells the approver to ask the sender to
    # extend it - but this path then refused the sender, because the share is
    # not active. The instruction and the code disagreed, and the only way out
    # was to discard the share and rebuild it (audit 2026-07-30).
    if share.state not in (ShareState.active, ShareState.pending_approval):
        raise AppError(
            409,
            "SHARE_NOT_ACTIVE",
            "Only an active or pending-approval share can have its expiry changed.",
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
) -> tuple[Share, list[PurgeEntry]]:
    """Owner-or-admin expires a share immediately: transitions state to
    `expired` and marks every file `deleted`, inside the caller's transaction.

    Returns `(share, to_purge)`. **The caller must commit and then call
    `file_svc.purge_expired_bytes(db, to_purge, reason=...)`** - unlinking bytes
    and releasing quota are irreversible and non-transactional, so doing them
    before the commit (which this used to) meant a commit failure left a row
    still marked `clean` whose bytes were already gone. Same two-phase shape as
    the hourly cron, which was restructured for this in audit M14.

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
    from .file import mark_deleted_for_expiry

    to_purge: list[PurgeEntry] = []
    for f in list(share.files):
        entry = mark_deleted_for_expiry(db, file=f)
        if entry is not None:
            to_purge.append(entry)

    record_audit_event(
        db,
        event_type=AuditEventType.share_expired,
        actor_user_id=user.id,
        target_type="share",
        target_id=share.id,
        metadata={"via": "owner_action", "file_count": len(to_purge)},
        request=request,
    )
    return share, to_purge


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
    if share.state not in (ShareState.active, ShareState.pending_approval):
        raise AppError(
            409,
            "SHARE_NOT_ACTIVE",
            "Only an active or pending-approval share can receive files.",
        )
    # `create_pending` attaches the file to a pending share on purpose - the
    # owner keeps assembling while it waits for approval - so by the time this
    # batch-complete signal ran, the files were ALREADY on the share. Refusing
    # here told the caller the batch had failed while the content had in fact
    # changed: the SPA reported an error, the owner re-uploaded, and the bytes
    # and the quota charge doubled, with no share-level audit row recording
    # either attempt (audit 2026-07-30). Recipients must still not hear about a
    # share that is not live yet, which the notify guard below handles.

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

    # If the share has not announced itself yet - the normal case for the very
    # first batch, since files attach at upload time - this IS the announcement,
    # not a "files were added" follow-up. Sending both would tell the recipient
    # about a share and then immediately about an addition to it.
    if announce_if_ready(db, share.id):
        return share

    # Never for a share still awaiting approval: the recipients cannot see it
    # yet, and telling them files were added to something they have no access
    # to is both confusing and a disclosure of the share's existence.
    #
    # A file added to an ALREADY-approved share is held for its own decision, so
    # the recipients must not hear about it either - they would be told about
    # content they get 409 on. Ping the approvers instead; the recipient notice
    # fires from `decide_added_files` once the file is released.
    from . import share_approval as approval_svc

    if added_count > 0 and approval_svc.files_awaiting_review(db, share):
        _notify_approvers_pending(db, share)
    elif notify and added_count > 0 and share.state == ShareState.active:
        _notify_recipients_files_added(db, share, actor=user, added_count=added_count)

    db.flush()
    return share


def _notify_recipients_files_added(
    db: Session, share: Share, *, actor: User, added_count: int
) -> None:
    """Tell a share's recipients that files were added to it. Extracted so the
    post-approval release path (`decide_added_files`) fires the same notice at
    the moment the files actually become reachable, rather than at upload time
    when they are still gated."""
    if added_count <= 0:
        return
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
    notify_user_ids.discard(actor.id)

    if notify_user_ids:
        base_url = site_svc.get_site_url(db)
        payload_base = {
            "sender_name": actor.display_name,
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


def invalidate_all_active_shares(
    db: Session, *, actor: User | None = None, request=None
) -> dict:
    """Expire every currently-active share and hard-delete its file bytes.

    Used by the config-restore flow: importing a configuration changes the
    world out from under any live share, so all of them are invalidated. Mirrors
    the hourly ``expire_files`` cron (flip state -> expired, mark every file
    deleted, audit ``share_expired`` per share) but with an admin actor and a
    single summary audit row.

    Two-phase like the cron: this marks rows inside the caller's transaction and
    the byte unlink happens after ``db.commit()``. The caller MUST run it in its
    own committed pass *before* the config transaction. Returns a small summary
    dict including ``to_purge`` for phase 2."""
    from sqlalchemy.orm import selectinload

    from .file import mark_deleted_for_expiry

    shares = (
        db.query(Share)
        .options(selectinload(Share.files))
        .filter(Share.state == ShareState.active)
        .all()
    )
    expired_shares = 0
    to_purge: list[PurgeEntry] = []
    for share in shares:
        file_count = 0
        for f in share.files:
            entry = mark_deleted_for_expiry(db, file=f)
            if entry is not None:
                to_purge.append(entry)
                file_count += 1
        share.state = ShareState.expired
        share.expires_at = utc_now()
        record_audit_event(
            db,
            event_type=AuditEventType.share_expired,
            actor_user_id=actor.id if actor else None,
            target_type="share",
            target_id=share.id,
            metadata={"file_count": file_count, "via": "config_restore"},
            request=request,
        )
        expired_shares += 1
    db.flush()
    logger.info(
        "invalidate_all_active_shares: expired %d shares, marked %d files",
        expired_shares, len(to_purge),
    )
    return {
        "expired_shares": expired_shares,
        "deleted_files": len(to_purge),
        "to_purge": to_purge,
    }
