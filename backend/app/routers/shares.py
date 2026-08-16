"""/api/shares/* - Phase 4 (multi-recipient + group recipients)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from ..config import settings
from ..dependencies import get_db, request_has_scope, require_scope
from ..middleware.errors import AppError
from ..models.file import FileApprovalState, FileState
from ..models.group import Group
from ..models.share import Share, ShareKind, ShareState
from ..models.share_recipient import ShareRecipient
from ..models.user import User, UserRole
from ..schemas.share import (
    ApproveShareRequest,
    BulkExpireFailure,
    BulkExpireRequest,
    BulkExpireResponse,
    CreateShareRequest,
    DecideAddedFilesRequest,
    FileInShareResponse,
    FilesAddedRequest,
    GroupRecipientRef,
    InlinePublicLinkResult,
    PublicLinkSummary,
    RejectShareRequest,
    ShareListItem,
    ShareListResponse,
    ShareRecipientRef,
    ShareResponse,
    ShareSenderRef,
    UpdateShareRequest,
)
from ..services import file as file_svc
from ..services import public_link as public_link_svc
from ..services import rate_limit as rate_limit_svc
from ..services import share as share_svc
from ..services import share_approval as share_approval_svc

# Per-sender share-creation rate limit (audit #2). Generous for any legitimate
# user; bounds an abusive/compromised account blasting unsolicited shares at
# staff/admins (staff->staff sends have no relationship requirement by design).
_SHARE_CREATE_LIMIT = 60
_SHARE_CREATE_WINDOW_SEC = 900  # 15 minutes

router = APIRouter(prefix="/api/shares", tags=["shares"])


def _visible_files(share) -> list:
    """Files the share's *current owner / recipients* should see. Excludes
    rows in ``state=deleted`` - the audit log + admin file history keep
    the historical record; user-facing surfaces shouldn't echo back what
    the user just deleted."""
    return [f for f in share.files if f.state != FileState.deleted]


def _effective_subject(share, files) -> str:
    """Subject if set, else first file's filename, else "" (frontend
    localises the empty case to "(no subject)").

    Callers should pass ``list(share.files)`` (every file ever in the
    share, including deleted ones) so a share whose last file the
    owner just deleted still has an identifiable label - otherwise
    the row turns into a faceless "(no subject)" tombstone."""
    if share.subject:
        return share.subject
    if files:
        return files[0].original_filename
    return ""


def _to_share_response(db: Session, share, *, viewer: User | None = None) -> ShareResponse:
    """Build a fully-hydrated ShareResponse: file list + user recipients +
    group recipients (resolved to id/name/is_company_inbox). `viewer` (the
    requesting user) populates `viewer_can_approve` for the Approve/Reject UI."""
    recipients = (
        db.query(ShareRecipient).filter(ShareRecipient.share_id == share.id).all()
    )
    rec_user_ids = [r.recipient_user_id for r in recipients if r.recipient_user_id is not None]
    group_ids = [r.recipient_group_id for r in recipients if r.recipient_group_id is not None]
    rec_groups: list[GroupRecipientRef] = []
    if group_ids:
        groups = db.query(Group).filter(Group.id.in_(group_ids)).all()
        rec_groups = [
            GroupRecipientRef(id=g.id, name=g.name, is_company_inbox=g.is_company_inbox)
            for g in groups
        ]
    # Co-recipient privacy (audit M4). ONE definition, shared with both list
    # routes - services/share.py::RosterVisibility. Identity maps when the
    # viewer is privileged; do not "optimise" that away with an `if`, which
    # reintroduces the branch the shared helper exists to remove.
    roster = share_svc.RosterVisibility(db, viewer, [share])
    rec_user_ids = [uid for uid in rec_user_ids if roster.allows_user(share.id, uid)]
    rec_groups = [g for g in rec_groups if roster.allows_group(share.id, g.id)]
    all_files = list(share.files)
    files = [f for f in all_files if f.state != FileState.deleted]
    files_pending = [
        f.id for f in files if f.approval_state == FileApprovalState.pending_review
    ]
    # A recipient must not even see the NAME of a file that is still awaiting
    # review - the gate exists so unreviewed content does not reach them, and a
    # filename is content. The owner keeps sight of their own upload, and
    # approvers need it to decide.
    #
    # `can_review_added_files` is the CONTENT-review predicate, deliberately -
    # NOT `can_decide_added_files`, which is what admits the approver to this
    # page in the first place (services/share.py::is_authorized_to_view). The
    # asymmetry is the feature: with content review off an approver may open the
    # share and cast the vote, but a filename is content and stays hidden.
    # "Aligning" this line to the view predicate is the leak.
    if files_pending and viewer is not None:
        may_see_pending = (
            viewer.id == share.created_by_id
            or share_approval_svc.can_review_added_files(db, viewer)
            or viewer.role == UserRole.admin
        )
        if not may_see_pending:
            files = [
                f
                for f in files
                if f.approval_state != FileApprovalState.pending_review
            ]
    elif files_pending and viewer is None:
        files = [
            f for f in files if f.approval_state != FileApprovalState.pending_review
        ]
    return ShareResponse(
        id=share.id,
        kind=share.kind,
        state=share.state,
        subject=share.subject,
        effective_subject=_effective_subject(share, all_files),
        message=share.message,
        created_at=share.created_at,
        expires_at=share.expires_at,
        created_by_id=share.created_by_id,
        recipient_user_ids=rec_user_ids,
        recipient_groups=rec_groups,
        files=[
            FileInShareResponse(
                id=f.id,
                original_filename=f.original_filename,
                mime_type=f.mime_type,
                size_bytes=f.size_bytes,
                state=f.state.value,
                created_at=f.created_at,
                finalized_at=f.finalized_at,
                sha256_hex=f.sha256_hex,
                av_unscanned=f.av_unscanned,
                approval_state=f.approval_state.value,
            )
            for f in files
        ],
        download_limit=share.download_limit,
        downloads_remaining=share.downloads_remaining,
        rejection_reason=share.rejection_reason,
        approval_decided_at=share.approval_decided_at,
        viewer_can_approve=(
            share_approval_svc.can_decide(db, viewer, share)
            if viewer is not None
            else False
        ),
        public_link_summary=_public_link_summary(db, share, viewer, roster),
        # Also populated for an ACTIVE share carrying appended files that are
        # still awaiting review - that decision echoes the digest back too, so
        # withholding it there would make the endpoint unusable.
        content_fingerprint=(
            share_approval_svc.content_fingerprint(db, share)
            if share.state == ShareState.pending_approval or files_pending
            else None
        ),
        files_awaiting_review=files_pending,
    )


def _public_link_summary(
    db: Session,
    share: Share,
    viewer: User | None,
    roster: share_svc.RosterVisibility,
) -> PublicLinkSummary | None:
    """Tell the owner, admins and approvers that a public link is attached.
    Approvers are the reason this exists: a link on a pending share is invisible
    to them and goes live on approval. Never carries the URL.

    The audience is the same one that may see the full roster, so it reuses that
    predicate rather than being a fourth hand-written copy of it."""
    # This guard stays FIRST and separate: `may_see_full` answers True for a
    # null viewer (no viewer, no projection), while a link summary for a null
    # viewer is None. The two semantics genuinely invert here.
    if viewer is None:
        return None
    if not roster.may_see_full(share.id):
        return None
    link = public_link_svc.get_active_link_for_share(db, share.id)
    if link is None:
        return None
    return PublicLinkSummary(
        has_password=link.password_hash is not None,
        download_limit=link.download_limit,
        downloads_remaining=link.downloads_remaining,
        created_at=link.created_at,
    )


def _public_link_url(token: str, db: Session) -> str:
    from ..services import site as site_svc

    return f"{site_svc.get_site_url(db)}{settings.PUBLIC_LINK_BASE_PATH}/{token}"


@router.post("", response_model=ShareResponse, status_code=status.HTTP_201_CREATED)
def create_share(
    payload: CreateShareRequest,
    request: Request,
    user: User = Depends(require_scope("shares:create")),
    db: Session = Depends(get_db),
) -> ShareResponse:
    # Per-sender rate limit (audit #2): cap how fast one account can create
    # shares, so a compromised/abusive user can't blast unsolicited content at
    # staff/admins. Keyed by user id; fail-open if Redis is down.
    if not rate_limit_svc.check_ip_allowed(
        "share_create", f"u{user.id}", _SHARE_CREATE_LIMIT, _SHARE_CREATE_WINDOW_SEC
    ):
        raise AppError(
            429, "RATE_LIMITED", "You're creating shares too quickly; try again shortly."
        )

    # A restricted API token with shares:create but WITHOUT public_links:write
    # must not expose files publicly via the inline-link shortcut. Checked
    # before the org policy gate and before any DB write.
    if payload.public_link is not None and not request_has_scope(
        request, "public_links:write"
    ):
        raise AppError(
            403,
            "INSUFFICIENT_SCOPE",
            "This API token lacks the required scope.",
            details={"required_scope": "public_links:write"},
        )

    # Pre-flight the public-link policy gate before any DB writes - no
    # half-created share if the user can't add the link they asked for.
    if payload.public_link is not None and not public_link_svc.is_allowed_to_create(
        db, user
    ):
        raise AppError(
            403,
            "PUBLIC_LINK_NOT_ALLOWED",
            "Your administrator has restricted public-link creation.",
        )

    # Kind is determined by role, not the client payload: a client always
    # creates an inbound (→ company) share; staff always create outbound. This
    # is the server-side enforcement of the share model (the SPA/desktop set it
    # too, but we never trust the client). Inbound ignores any recipients.
    kind = ShareKind.inbound if user.role == UserRole.client else ShareKind.outbound

    share = share_svc.create_share(
        db,
        created_by=user,
        kind=kind,
        recipient_user_ids=payload.recipients.user_ids,
        recipient_group_ids=payload.recipients.group_ids,
        expires_at=payload.expires_at,
        subject=payload.subject,
        message=payload.message,
        # Service-level recipients-required guard is relaxed when an
        # inline public link is being attached - the link is the access
        # mechanism. The schema validator enforces "recipients OR
        # public_link" at the API boundary; this kwarg keeps the service
        # honest for direct callers.
        allow_no_recipients=payload.public_link is not None,
        notify_recipients=payload.notify_recipients,
        download_limit=payload.download_limit,
        request=request,
    )

    public_link_inline: InlinePublicLinkResult | None = None
    if payload.public_link is not None:
        created = public_link_svc.create_link(
            db,
            actor=user,
            share=share,
            password=payload.public_link.password,
            download_limit=payload.public_link.download_limit,
            notify_on_download=payload.public_link.notify_on_download,
            request=request,
        )
        inline_url = _public_link_url(created.plaintext_token, db)
        from ..utils.qr import render_qr_svg
        public_link_inline = InlinePublicLinkResult(
            id=created.record.id,
            url=inline_url,
            qr_svg=render_qr_svg(inline_url),
            download_limit=created.record.download_limit,
            downloads_remaining=created.record.downloads_remaining,
            notify_on_download=created.record.notify_on_download,
            has_password=created.record.password_hash is not None,
            created_at=created.record.created_at,
        )

    db.commit()
    db.refresh(share)
    response = _to_share_response(db, share, viewer=user)
    if public_link_inline is not None:
        response.public_link = public_link_inline
    return response


@router.get("", response_model=ShareListResponse)
def list_shares(
    box: str = Query("outbox", pattern="^(outbox|inbox)$"),
    q: str = Query("", max_length=255),
    state: list[str] = Query(default_factory=list),  # noqa: B008
    recipient_user_id: int | None = Query(None, ge=1),
    recipient_group_id: int | None = Query(None, ge=1),
    sender_user_id: int | None = Query(None, ge=1),
    via_group_id: int | None = Query(None, ge=1),
    sort: str = Query("created_at"),
    direction: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
    user: User = Depends(require_scope("shares:read")),
    db: Session = Depends(get_db),
) -> ShareListResponse:
    rows, total = share_svc.list_shares_for_user(
        db,
        user=user,
        box=box,
        q=q,
        states=state or None,
        recipient_user_id=recipient_user_id,
        recipient_group_id=recipient_group_id,
        sender_user_id=sender_user_id,
        via_group_id=via_group_id,
        sort=sort,
        direction=direction,
        page=page,
        page_size=page_size,
    )

    # Bulk-load recipients + senders to avoid N+1 across the page.
    share_ids = [s.id for s in rows]
    recipient_rows = (
        db.query(ShareRecipient)
        .filter(ShareRecipient.share_id.in_(share_ids))
        .all()
        if share_ids
        else []
    )
    recipient_user_ids: set[int] = set()
    recipient_group_ids: set[int] = set()
    for r in recipient_rows:
        if r.recipient_user_id is not None:
            recipient_user_ids.add(r.recipient_user_id)
        if r.recipient_group_id is not None:
            recipient_group_ids.add(r.recipient_group_id)

    # For inbox views the sender is interesting; for outbox it's always
    # the requester so we skip the lookup.
    sender_user_ids: set[int] = (
        {s.created_by_id for s in rows} if box == "inbox" else set()
    )

    user_ids_to_fetch = recipient_user_ids | sender_user_ids
    users_by_id: dict[int, User] = (
        {u.id: u for u in db.query(User).filter(User.id.in_(user_ids_to_fetch)).all()}
        if user_ids_to_fetch
        else {}
    )
    groups_by_id: dict[int, Group] = (
        {g.id: g for g in db.query(Group).filter(Group.id.in_(recipient_group_ids)).all()}
        if recipient_group_ids
        else {}
    )

    # Recipients per share, materialised as ShareRecipientRef rows.
    #
    # Same co-recipient privacy rule the DETAIL serialiser and the approvals
    # queue apply - it was missing here, so `GET /api/shares?box=inbox` handed
    # any recipient the display name and role of every other recipient plus the
    # name of every group the share was addressed to. That is strictly more than
    # the detail route discloses even to a fully privileged viewer, which
    # exposes only user ids. Reachable by the least-privileged role on stock
    # configuration.
    #
    # Creators, admins and approvers see the full roster, as they do on detail.
    # One definition for all three routes: services/share.py::RosterVisibility.
    roster = share_svc.RosterVisibility(db, user, rows)

    recips_by_share: dict[str, list[ShareRecipientRef]] = {sid: [] for sid in share_ids}
    for r in recipient_rows:
        if r.recipient_user_id is not None:
            if not roster.allows_user(r.share_id, r.recipient_user_id):
                continue
            u = users_by_id.get(r.recipient_user_id)
            if u is not None:
                recips_by_share[r.share_id].append(
                    ShareRecipientRef(
                        kind="user",
                        id=u.id,
                        label=u.display_name,
                        role=u.role.value,
                    )
                )
        elif r.recipient_group_id is not None:
            if not roster.allows_group(r.share_id, r.recipient_group_id):
                continue
            g = groups_by_id.get(r.recipient_group_id)
            if g is not None:
                recips_by_share[r.share_id].append(
                    ShareRecipientRef(kind="group", id=g.id, label=g.name)
                )

    items: list[ShareListItem] = []
    for s in rows:
        all_files = list(s.files)
        files = [f for f in all_files if f.state != FileState.deleted]
        sender: ShareSenderRef | None = None
        if box == "inbox":
            su = users_by_id.get(s.created_by_id)
            if su is not None:
                sender = ShareSenderRef(
                    id=su.id, display_name=su.display_name, email=su.email
                )
        # Inbound (client → company) shares carry no recipient rows - the
        # audience is "the company". Surface a single synthetic recipient so the
        # UI renders "→ Company" (the SPA translates on kind="company").
        if s.kind == ShareKind.inbound:
            recips = [ShareRecipientRef(kind="company", id=0, label="Company")]
        else:
            recips = recips_by_share.get(s.id, [])
        items.append(
            ShareListItem(
                id=s.id,
                kind=s.kind,
                state=s.state,
                subject=s.subject,
                effective_subject=_effective_subject(s, all_files),
                created_at=s.created_at,
                expires_at=s.expires_at,
                created_by_id=s.created_by_id,
                file_count=len(files),
                total_size_bytes=sum(f.size_bytes for f in files),
                download_limit=s.download_limit,
                downloads_remaining=s.downloads_remaining,
                recipients=recips,
                sender=sender,
            )
        )
    return ShareListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/pending-approval", response_model=ShareListResponse)
def list_pending_approval(
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
    user: User = Depends(require_scope("shares:read")),
    db: Session = Depends(get_db),
) -> ShareListResponse:
    """Shares awaiting the current user's approval (approver, not their own),
    oldest first. Empty when the user isn't an approver. Defined BEFORE
    `/{share_id}` so the literal path wins over the wildcard."""
    rows, total = share_svc.list_pending_approvals(
        db, user=user, page=page, page_size=page_size
    )
    share_ids = [s.id for s in rows]
    recipient_rows = (
        db.query(ShareRecipient).filter(ShareRecipient.share_id.in_(share_ids)).all()
        if share_ids
        else []
    )
    rec_user_ids = {r.recipient_user_id for r in recipient_rows if r.recipient_user_id}
    rec_group_ids = {r.recipient_group_id for r in recipient_rows if r.recipient_group_id}
    sender_ids = {s.created_by_id for s in rows}
    users_by_id = (
        {u.id: u for u in db.query(User).filter(User.id.in_(rec_user_ids | sender_ids)).all()}
        if (rec_user_ids | sender_ids)
        else {}
    )
    groups_by_id = (
        {g.id: g for g in db.query(Group).filter(Group.id.in_(rec_group_ids)).all()}
        if rec_group_ids
        else {}
    )
    # Co-recipient privacy, same rule and same object as the other two routes.
    # This was the one recipient loop written without it: harmless while every
    # queue row was a share the viewer could decide (and so see in full), and a
    # disclosure the moment v2.13.1 added ACTIVE shares carrying appended files.
    # A `pending_approval` row still yields the full roster - an approver must
    # see who a share is for; an active row projects, exactly as detail does for
    # the same viewer.
    #
    # Project at CONSTRUCTION, never by narrowing the bulk loads above:
    # `users_by_id` also resolves the sender ref below.
    roster = share_svc.RosterVisibility(db, user, rows)
    recips_by_share: dict[str, list[ShareRecipientRef]] = {sid: [] for sid in share_ids}
    for r in recipient_rows:
        if r.recipient_user_id is not None:
            if not roster.allows_user(r.share_id, r.recipient_user_id):
                continue
            u = users_by_id.get(r.recipient_user_id)
            if u is not None:
                recips_by_share[r.share_id].append(
                    ShareRecipientRef(kind="user", id=u.id, label=u.display_name, role=u.role.value)
                )
        elif r.recipient_group_id is not None:
            if not roster.allows_group(r.share_id, r.recipient_group_id):
                continue
            g = groups_by_id.get(r.recipient_group_id)
            if g is not None:
                recips_by_share[r.share_id].append(
                    ShareRecipientRef(kind="group", id=g.id, label=g.name)
                )
    items: list[ShareListItem] = []
    for s in rows:
        all_files = list(s.files)
        files = [f for f in all_files if f.state != FileState.deleted]
        su = users_by_id.get(s.created_by_id)
        sender = (
            ShareSenderRef(id=su.id, display_name=su.display_name, email=su.email)
            if su is not None
            else None
        )
        recips = (
            [ShareRecipientRef(kind="company", id=0, label="Company")]
            if s.kind == ShareKind.inbound
            else recips_by_share.get(s.id, [])
        )
        items.append(
            ShareListItem(
                id=s.id,
                kind=s.kind,
                state=s.state,
                subject=s.subject,
                effective_subject=_effective_subject(s, all_files),
                created_at=s.created_at,
                expires_at=s.expires_at,
                created_by_id=s.created_by_id,
                file_count=len(files),
                total_size_bytes=sum(f.size_bytes for f in files),
                download_limit=s.download_limit,
                downloads_remaining=s.downloads_remaining,
                recipients=recips,
                sender=sender,
            )
        )
    return ShareListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/{share_id}/approve", response_model=ShareResponse)
def approve_share_route(
    share_id: str,
    request: Request,
    payload: ApproveShareRequest,
    user: User = Depends(require_scope("shares:manage")),
    db: Session = Depends(get_db),
) -> ShareResponse:
    """Approver approves a pending share → active; recipients are notified now.

    The body is REQUIRED now, and so is its `content_fingerprint`: an approval
    that does not say what it is approving is not a four-eyes control. This is a
    deliberate breaking change for API-token clients written against the
    one release where the field was optional."""
    share = share_svc.get_share_or_404(db, share_id)
    share_svc.approve_share(
        db,
        user=user,
        share=share,
        request=request,
        expect_fingerprint=payload.content_fingerprint,
    )
    db.commit()
    db.refresh(share)
    return _to_share_response(db, share, viewer=user)


@router.post("/{share_id}/added-files/decide", response_model=ShareResponse)
def decide_added_files_route(
    share_id: str,
    payload: DecideAddedFilesRequest,
    request: Request,
    user: User = Depends(require_scope("shares:manage")),
    db: Session = Depends(get_db),
) -> ShareResponse:
    """Approver releases (or refuses) files appended to an already-approved
    share. The share itself stays `active` throughout - only the new files are
    gated, so existing recipients keep their access while these wait."""
    share = share_svc.get_share_or_404(db, share_id)
    _, to_purge = share_svc.decide_added_files(
        db,
        user=user,
        share=share,
        approve=payload.approve,
        expect_fingerprint=payload.content_fingerprint,
        reason=payload.reason,
        request=request,
    )
    db.commit()
    # Bytes go after the commit - the ordering v2.5.0 established.
    if to_purge:
        file_svc.purge_locators(db, to_purge, reason="approval_rejected")
    db.refresh(share)
    return _to_share_response(db, share, viewer=user)


@router.post("/{share_id}/reject", response_model=ShareResponse)
def reject_share_route(
    share_id: str,
    payload: RejectShareRequest,
    request: Request,
    user: User = Depends(require_scope("shares:manage")),
    db: Session = Depends(get_db),
) -> ShareResponse:
    """Approver rejects a pending share → rejected (files kept); sender notified."""
    share = share_svc.get_share_or_404(db, share_id)
    share_svc.reject_share(
        db, user=user, share=share, reason=payload.reason, request=request
    )
    db.commit()
    db.refresh(share)
    return _to_share_response(db, share, viewer=user)


@router.post("/{share_id}/resubmit", response_model=ShareResponse)
def resubmit_share_route(
    share_id: str,
    request: Request,
    user: User = Depends(require_scope("shares:manage")),
    db: Session = Depends(get_db),
) -> ShareResponse:
    """Owner re-queues a rejected share for approval."""
    share = share_svc.get_share_or_404(db, share_id)
    share_svc.resubmit_share(db, user=user, share=share, request=request)
    db.commit()
    db.refresh(share)
    return _to_share_response(db, share, viewer=user)


@router.get("/{share_id}", response_model=ShareResponse)
def get_share(
    share_id: str,
    user: User = Depends(require_scope("shares:read")),
    db: Session = Depends(get_db),
) -> ShareResponse:
    share = share_svc.get_share_or_404(db, share_id)
    if not share_svc.is_authorized_to_view(db, user=user, share=share):
        raise AppError(403, "FORBIDDEN", "You don't have access to this share.")
    return _to_share_response(db, share, viewer=user)


@router.delete("/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_share(
    share_id: str,
    request: Request,
    user: User = Depends(require_scope("shares:manage")),
    db: Session = Depends(get_db),
) -> None:
    share = share_svc.get_share_or_404(db, share_id)
    share_svc.revoke_share(db, user=user, share=share, request=request)
    db.commit()


@router.patch("/{share_id}", response_model=ShareResponse)
def patch_share(
    share_id: str,
    payload: UpdateShareRequest,
    request: Request,
    user: User = Depends(require_scope("shares:manage")),
    db: Session = Depends(get_db),
) -> ShareResponse:
    """Editable fields on an active share: expires_at, download_limit.
    All optional - only supplied fields change.

    For expires_at: supply a datetime to set, or `expires_at_clear: true`
    to remove the expiry (share becomes never-expire). The two are
    mutually exclusive - sending both is a 400.
    """
    share = share_svc.get_share_or_404(db, share_id)
    # Authorize BEFORE branching. The owner-or-admin check used to live only
    # inside update_share_expiry / update_share_limit, so a PATCH whose body
    # changed nothing skipped both and still fell through to the serializer -
    # turning this into a share-metadata read for any authenticated caller
    # holding the shares:manage scope (audit 2026-07-30).
    if share.created_by_id != user.id and user.role != UserRole.admin:
        raise AppError(403, "FORBIDDEN", "You cannot edit this share.")
    if payload.expires_at_clear and payload.expires_at is not None:
        raise AppError(
            400,
            "INVALID_INPUT",
            "Cannot supply both expires_at and expires_at_clear; they are mutually exclusive.",
        )
    if payload.expires_at_clear:
        share_svc.update_share_expiry(
            db,
            user=user,
            share=share,
            new_expires_at=None,
            request=request,
        )
    elif payload.expires_at is not None:
        share_svc.update_share_expiry(
            db,
            user=user,
            share=share,
            new_expires_at=payload.expires_at,
            request=request,
        )
    if payload.download_limit is not None or payload.download_limit_clear:
        share_svc.update_share_limit(
            db,
            user=user,
            share=share,
            new_limit=payload.download_limit,
            clear=payload.download_limit_clear,
            request=request,
        )
    db.commit()
    db.refresh(share)
    return _to_share_response(db, share, viewer=user)


@router.post("/{share_id}/expire", response_model=ShareResponse)
def expire_share_now_route(
    share_id: str,
    request: Request,
    user: User = Depends(require_scope("shares:manage")),
    db: Session = Depends(get_db),
) -> ShareResponse:
    """Owner-or-admin force-expires a share: state=expired, expires_at=now,
    every file hard-deleted from disk. Re-uses the same helper the
    hourly cron uses."""
    share = share_svc.get_share_or_404(db, share_id)
    _, to_purge = share_svc.expire_share_now(
        db, user=user, share=share, request=request
    )
    db.commit()
    # Bytes go only after the state flip is durable - see expire_share_now.
    file_svc.purge_expired_bytes(db, to_purge, reason="expire_now")
    db.refresh(share)
    return _to_share_response(db, share, viewer=user)


@router.post("/{share_id}/files-added", response_model=ShareResponse)
def files_added_route(
    share_id: str,
    payload: FilesAddedRequest,
    request: Request,
    user: User = Depends(require_scope("shares:add_files")),
    db: Session = Depends(get_db),
) -> ShareResponse:
    """Owner's batch-complete signal after uploading more files into an
    active share. Records a share-level audit row and, when
    `notify`, re-notifies the share's recipients. The files themselves were
    already attached by the upload pipeline (owner + active gated)."""
    share = share_svc.get_share_or_404(db, share_id)
    share_svc.register_files_added(
        db,
        user=user,
        share=share,
        file_ids=payload.file_ids,
        notify=payload.notify,
        request=request,
    )
    db.commit()
    db.refresh(share)
    return _to_share_response(db, share, viewer=user)


_BULK_EXPIRE_CAP = 100


@router.post("/bulk-expire", response_model=BulkExpireResponse)
def bulk_expire(
    payload: BulkExpireRequest,
    request: Request,
    user: User = Depends(require_scope("shares:manage")),
    db: Session = Depends(get_db),
) -> BulkExpireResponse:
    """Expire many shares in one request. Per-share commit so a single
    failure (404 / 403 / 409 SHARE_NOT_ACTIVE from a concurrent expire)
    doesn't abort the rest. Capped at 100 IDs per request."""
    if not payload.share_ids:
        raise AppError(400, "INVALID_INPUT", "No share IDs provided.")
    if len(payload.share_ids) > _BULK_EXPIRE_CAP:
        raise AppError(
            400,
            "BULK_TOO_LARGE",
            f"At most {_BULK_EXPIRE_CAP} shares per request.",
        )

    expired: list[str] = []
    failed: list[BulkExpireFailure] = []
    for sid in payload.share_ids:
        try:
            share = share_svc.get_share_or_404(db, sid)
            _, to_purge = share_svc.expire_share_now(
                db, user=user, share=share, request=request
            )
            db.commit()
            file_svc.purge_expired_bytes(db, to_purge, reason="bulk_expire")
            expired.append(sid)
        except AppError as e:
            db.rollback()
            failed.append(
                BulkExpireFailure(id=sid, code=e.code, message=e.message)
            )
        except Exception as e:
            db.rollback()
            failed.append(
                BulkExpireFailure(
                    id=sid, code="INTERNAL_ERROR", message=str(e)[:200]
                )
            )
    return BulkExpireResponse(expired=expired, failed=failed)
