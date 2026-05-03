"""/api/shares/* — Phase 4 (multi-recipient + group recipients)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from ..config import settings
from ..dependencies import get_actor, get_db
from ..middleware.errors import AppError
from ..models.file import FileState
from ..models.group import Group
from ..models.share_recipient import ShareRecipient
from ..models.user import User
from ..schemas.share import (
    CreateShareRequest,
    FileInShareResponse,
    GroupRecipientRef,
    InlinePublicLinkResult,
    ShareListItem,
    ShareListResponse,
    ShareRecipientRef,
    ShareResponse,
    ShareSenderRef,
    UpdateShareRequest,
)
from ..services import public_link as public_link_svc
from ..services import share as share_svc

router = APIRouter(prefix="/api/shares", tags=["shares"])


def _visible_files(share) -> list:
    """Files the share's *current owner / recipients* should see. Excludes
    rows in ``state=deleted`` — the audit log + admin file history keep
    the historical record; user-facing surfaces shouldn't echo back what
    the user just deleted."""
    return [f for f in share.files if f.state != FileState.deleted]


def _effective_subject(share, files) -> str:
    """Subject if set, else first file's filename, else "" (frontend
    localises the empty case to "(no subject)").

    Callers should pass ``list(share.files)`` (every file ever in the
    share, including deleted ones) so a share whose last file the
    owner just deleted still has an identifiable label — otherwise
    the row turns into a faceless "(no subject)" tombstone."""
    if share.subject:
        return share.subject
    if files:
        return files[0].original_filename
    return ""


def _to_share_response(db: Session, share) -> ShareResponse:
    """Build a fully-hydrated ShareResponse: file list + user recipients +
    group recipients (resolved to id/name/is_company_inbox)."""
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
    all_files = list(share.files)
    files = [f for f in all_files if f.state != FileState.deleted]
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
            )
            for f in files
        ],
    )


def _public_link_url(token: str, db: Session) -> str:
    from ..services import site as site_svc

    return f"{site_svc.get_site_url(db)}{settings.PUBLIC_LINK_BASE_PATH}/{token}"


@router.post("", response_model=ShareResponse, status_code=status.HTTP_201_CREATED)
def create_share(
    payload: CreateShareRequest,
    request: Request,
    user: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> ShareResponse:
    # Pre-flight the public-link policy gate before any DB writes — no
    # half-created share if the user can't add the link they asked for.
    if payload.public_link is not None and not public_link_svc.is_allowed_to_create(
        db, user
    ):
        raise AppError(
            403,
            "PUBLIC_LINK_NOT_ALLOWED",
            "Your administrator has restricted public-link creation.",
        )

    share = share_svc.create_share(
        db,
        created_by=user,
        kind=payload.kind,
        recipient_user_ids=payload.recipients.user_ids,
        recipient_group_ids=payload.recipients.group_ids,
        expires_at=payload.expires_at,
        subject=payload.subject,
        message=payload.message,
        # Service-level recipients-required guard is relaxed when an
        # inline public link is being attached — the link is the access
        # mechanism. The schema validator enforces "recipients OR
        # public_link" at the API boundary; this kwarg keeps the service
        # honest for direct callers.
        allow_no_recipients=payload.public_link is not None,
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
        public_link_inline = InlinePublicLinkResult(
            id=created.record.id,
            url=_public_link_url(created.plaintext_token, db),
            download_limit=created.record.download_limit,
            downloads_remaining=created.record.downloads_remaining,
            notify_on_download=created.record.notify_on_download,
            has_password=created.record.password_hash is not None,
            created_at=created.record.created_at,
        )

    db.commit()
    db.refresh(share)
    response = _to_share_response(db, share)
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
    user: User = Depends(get_actor),
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
    recips_by_share: dict[str, list[ShareRecipientRef]] = {sid: [] for sid in share_ids}
    for r in recipient_rows:
        if r.recipient_user_id is not None:
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
                recipients=recips_by_share.get(s.id, []),
                sender=sender,
            )
        )
    return ShareListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/{share_id}", response_model=ShareResponse)
def get_share(
    share_id: str,
    user: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> ShareResponse:
    share = share_svc.get_share_or_404(db, share_id)
    if not share_svc.is_authorized_to_download(db, user=user, share=share):
        raise AppError(403, "FORBIDDEN", "You don't have access to this share.")
    return _to_share_response(db, share)


@router.delete("/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_share(
    share_id: str,
    request: Request,
    user: User = Depends(get_actor),
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
    user: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> ShareResponse:
    """Editable fields on an active share. Today: only `expires_at`."""
    share = share_svc.get_share_or_404(db, share_id)
    share_svc.update_share_expiry(
        db,
        user=user,
        share=share,
        new_expires_at=payload.expires_at,
        request=request,
    )
    db.commit()
    db.refresh(share)
    return _to_share_response(db, share)


@router.post("/{share_id}/expire", response_model=ShareResponse)
def expire_share_now_route(
    share_id: str,
    request: Request,
    user: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> ShareResponse:
    """Owner-or-admin force-expires a share: state=expired, expires_at=now,
    every file hard-deleted from disk. Re-uses the same helper the
    hourly cron uses."""
    share = share_svc.get_share_or_404(db, share_id)
    share_svc.expire_share_now(db, user=user, share=share, request=request)
    db.commit()
    db.refresh(share)
    return _to_share_response(db, share)
