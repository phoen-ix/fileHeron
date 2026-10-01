"""/api/secrets/* - send, list and read secrets (v2.24.0).

Only `POST /{id}/reveal` ever returns the content, and only to a recipient. The
sender and admins get metadata: who it went to, who viewed it when, how many
views are left. See services/secret.py and services/secret_reveal.py.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from ..dependencies import get_db, request_has_scope, require_scope
from ..middleware.errors import AppError
from ..models.group import Group
from ..models.secret import (
    Secret,
    SecretGroupMember,
    SecretRecipient,
    SecretRecipientKind,
    SecretState,
    SecretUserState,
    SecretViewScope,
)
from ..models.user import User, UserRole
from ..schemas.secret import (
    CreateSecretRequest,
    RevealSecretRequest,
    RevealSecretResponse,
    SecretEvent,
    SecretGroupRef,
    SecretLinkItem,
    SecretLinkResponse,
    SecretLinksResponse,
    SecretListItem,
    SecretListResponse,
    SecretMemberStatus,
    SecretRecipientStatus,
    SecretRecipientSummary,
    SecretResponse,
    SecretUserRef,
)
from ..services import rate_limit as rate_limit_svc
from ..services import secret as secret_svc
from ..services import secret_reveal
from ..utils.client_ip import get_client_ip
from ..utils.qr import render_qr_svg

router = APIRouter(prefix="/api/secrets", tags=["secrets"])

# Per-sender creation limit, the share-create figure: generous for people,
# a bound on a compromised account mailing links to arbitrary addresses.
_CREATE_LIMIT = 60
_CREATE_WINDOW_SEC = 900

# Nothing between the server and the browser may keep a copy of a reveal.
NO_STORE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}


def _no_store(response: Response) -> None:
    for k, v in NO_STORE.items():
        response.headers[k] = v


def _user_ref(user: User | None) -> SecretUserRef:
    if user is None:
        return SecretUserRef(id=0, display_name="")
    return SecretUserRef(id=user.id, display_name=user.display_name)


def summary(recipients: list[SecretRecipient]) -> SecretRecipientSummary:
    kinds = [r.kind for r in recipients]
    return SecretRecipientSummary(
        users=kinds.count(SecretRecipientKind.user),
        groups=kinds.count(SecretRecipientKind.group),
        emails=kinds.count(SecretRecipientKind.email),
        link=any(
            r.kind == SecretRecipientKind.link and r.revoked_at is None for r in recipients
        ),
    )


def _recipients(db: Session, secret: Secret) -> list[SecretRecipient]:
    return (
        db.query(SecretRecipient)
        .filter(SecretRecipient.secret_id == secret.id)
        .order_by(SecretRecipient.id)
        .all()
    )


def _roster(
    db: Session, secret: Secret, recipients: list[SecretRecipient]
) -> list[SecretRecipientStatus]:
    """Every recipient with its counts - for the sender and admins only."""
    reach = secret_svc.all_account_reach(db, secret)
    states = {
        s.user_id: s
        for s in db.query(SecretUserState).filter(SecretUserState.secret_id == secret.id)
    }
    snapshot: dict[int, list[int]] = {}
    group_rec_ids = [r.id for r in recipients if r.kind == SecretRecipientKind.group]
    if group_rec_ids:
        for row in (
            db.query(SecretGroupMember)
            .filter(SecretGroupMember.recipient_id.in_(group_rec_ids))
            .order_by(SecretGroupMember.recipient_id, SecretGroupMember.user_id)
        ):
            snapshot.setdefault(row.recipient_id, []).append(row.user_id)
    user_ids = set(states) | {
        r.recipient_user_id for r in recipients if r.recipient_user_id is not None
    }
    users = (
        {u.id: u for u in db.query(User).filter(User.id.in_(user_ids))} if user_ids else {}
    )
    group_ids = [r.recipient_group_id for r in recipients if r.recipient_group_id is not None]
    groups = (
        {g.id: g for g in db.query(Group).filter(Group.id.in_(group_ids))} if group_ids else {}
    )

    def eligible(uid: int) -> bool:
        u = users.get(uid)
        r = reach.get(uid)
        return u is not None and not u.is_disabled and r is not None and r.eligible

    out: list[SecretRecipientStatus] = []
    for rec in recipients:
        status_row = SecretRecipientStatus(
            id=rec.id,
            kind=rec.kind,
            email=rec.email,
            views_used=rec.views_used,
            views_left=None,
            failed_attempts=rec.failed_attempts,
            locked_until=rec.locked_until,
            burned=rec.burned_at is not None,
            revoked=rec.revoked_at is not None,
            emailed_at=rec.notified_at,
            created_at=rec.created_at,
        )
        if rec.kind == SecretRecipientKind.user and rec.recipient_user_id is not None:
            uid = rec.recipient_user_id
            status_row.user = _user_ref(users.get(uid))
            st = states.get(uid)
            if st is not None:
                status_row.views_used = st.views_used
                status_row.failed_attempts = st.failed_attempts
                status_row.burned = st.burned_at is not None
                r = reach.get(uid)
                if r is not None:
                    status_row.views_left = secret_svc.views_left_for_user(secret, st, r)
        elif rec.kind == SecretRecipientKind.group and rec.recipient_group_id is not None:
            g = groups.get(rec.recipient_group_id)
            status_row.group = SecretGroupRef(
                id=rec.recipient_group_id, name=g.name if g is not None else ""
            )
            members = []
            for uid in snapshot.get(rec.id, []):
                st = states.get(uid)
                members.append(
                    SecretMemberStatus(
                        user=_user_ref(users.get(uid)),
                        views_used=st.views_used if st is not None else 0,
                        last_viewed_at=st.last_viewed_at if st is not None else None,
                        eligible=eligible(uid),
                        burned=st is not None and st.burned_at is not None,
                    )
                )
            status_row.members = members
            if secret.view_scope != SecretViewScope.per_recipient:
                status_row.views_used = sum(m.views_used for m in members)
            if secret.max_views is not None:
                if secret.view_scope == SecretViewScope.per_recipient:
                    status_row.views_left = max(0, secret.max_views - rec.views_used)
                elif secret.view_scope == SecretViewScope.total:
                    status_row.views_left = max(0, secret.max_views - secret.views_used)
        else:
            status_row.views_left = secret_svc.views_left_for_recipient(secret, rec)
        out.append(status_row)
    return out


def _events(
    db: Session, secret: Secret, recipients: list[SecretRecipient], *, show_all_ips: bool
) -> list[SecretEvent]:
    by_id = {r.id: r for r in recipients}
    events = secret_svc.recent_events(db, secret)
    uids = {e.user_id for e in events if e.user_id is not None}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(uids))} if uids else {}
    out = []
    for e in events:
        rec = by_id.get(e.recipient_id) if e.recipient_id is not None else None
        out.append(
            SecretEvent(
                at=e.created_at,
                outcome=e.outcome,
                recipient_id=e.recipient_id,
                kind=rec.kind if rec is not None else None,
                user=_user_ref(users.get(e.user_id)) if e.user_id is not None else None,
                email=rec.email if rec is not None else None,
                # An address/link view has no other identity; a colleague's own
                # address is for an admin, not for the sender.
                ip=e.ip if (show_all_ips or e.user_id is None) else None,
            )
        )
    return out


def to_response(db: Session, secret: Secret, viewer: User) -> SecretResponse:
    is_sender = viewer.id == secret.created_by_id
    is_admin = viewer.role == UserRole.admin
    state = secret_svc.user_state(db, secret, viewer.id)
    role: Literal["sender", "admin", "recipient"] = (
        "sender" if is_sender else "recipient" if state is not None else "admin"
    )
    recipients = _recipients(db, secret)
    resp = SecretResponse(
        id=secret.id,
        state=secret.state,
        label=secret.label,
        sender=_user_ref(secret.created_by),
        created_at=secret.created_at,
        ended_at=secret.ended_at,
        expires_at=secret.expires_at,
        max_views=secret.max_views,
        view_scope=secret.view_scope,
        views_used=secret.views_used if is_sender or is_admin else None,
        has_passphrase=secret.has_passphrase,
        notify_on_view=secret.notify_on_view,
        burn_after_failures=secret.burn_after_failures,
        viewer_role=role,
    )
    if state is not None:
        st = secret_reveal.standing(db, secret, viewer)
        resp.my_views_left = st.views_left
        resp.can_reveal = secret_reveal.can_reveal(secret, st)
        resp.still_recipient = st.reach.eligible
        resp.burned_for_me = st.burned
        resp.my_failed_attempts = state.failed_attempts
    if is_sender or is_admin:
        resp.recipient_summary = summary(recipients)
        resp.recipients = _roster(db, secret, recipients)
        resp.events = _events(db, secret, recipients, show_all_ips=is_admin)
    return resp


@router.post("", response_model=SecretResponse, status_code=status.HTTP_201_CREATED)
def create_secret(
    payload: CreateSecretRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_scope("secrets:send")),
    db: Session = Depends(get_db),
) -> SecretResponse:
    if not rate_limit_svc.check_ip_allowed(
        "secret_create", f"u{user.id}", _CREATE_LIMIT, _CREATE_WINDOW_SEC
    ):
        raise AppError(
            429, "RATE_LIMITED", "You're sending secrets too quickly; try again shortly."
        )
    created = secret_svc.create_secret(
        db,
        sender=user,
        content=payload.content,
        label=payload.label,
        passphrase=payload.passphrase,
        max_views=payload.max_views,
        view_scope=payload.view_scope,
        expires_at=payload.expires_at,
        user_ids=payload.recipients.user_ids,
        group_ids=payload.recipients.group_ids,
        emails=payload.recipients.emails,
        create_link=payload.create_link,
        notify_on_view=payload.notify_on_view,
        burn_on_failures=payload.burn_on_failures,
        request=request,
    )
    db.commit()
    db.refresh(created.secret)
    resp = to_response(db, created.secret, user)
    if created.link_token is not None:
        url = secret_svc.secret_url(db, created.link_token)
        resp.link_url = url
        resp.link_qr_svg = render_qr_svg(url)
        _no_store(response)
    return resp


@router.get("", response_model=SecretListResponse)
def list_secrets(
    box: str = Query("received", pattern="^(received|sent)$"),
    state: list[SecretState] = Query(default_factory=list),  # noqa: B008
    q: str = Query("", max_length=200),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
    user: User = Depends(require_scope("secrets:read")),
    db: Session = Depends(get_db),
) -> SecretListResponse:
    rows, total = secret_svc.list_for_user(
        db,
        user=user,
        box=box,
        states=state or None,
        q=q,
        page=page,
        page_size=page_size,
    )
    items = []
    for s in rows:
        item = SecretListItem(
            id=s.id,
            state=s.state,
            label=s.label,
            sender=_user_ref(s.created_by),
            created_at=s.created_at,
            ended_at=s.ended_at,
            expires_at=s.expires_at,
            max_views=s.max_views,
            view_scope=s.view_scope,
            views_used=s.views_used if box == "sent" else None,
            has_passphrase=s.has_passphrase,
        )
        if box == "sent":
            item.recipient_summary = summary(_recipients(db, s))
        else:
            item.my_views_left = secret_reveal.standing(db, s, user).views_left
        items.append(item)
    return SecretListResponse(items=items, total=total, page=page, page_size=page_size)


def _visible_or_404(db: Session, secret_id: str, user: User) -> Secret:
    secret = secret_svc.get_or_404(db, secret_id)
    if secret_svc.viewer_role(db, secret, user) is None:
        raise AppError(404, "SECRET_NOT_FOUND", "This secret does not exist.")
    return secret


@router.get("/{secret_id}", response_model=SecretResponse)
def get_secret(
    secret_id: str,
    user: User = Depends(require_scope("secrets:read")),
    db: Session = Depends(get_db),
) -> SecretResponse:
    return to_response(db, _visible_or_404(db, secret_id, user), user)


@router.post("/{secret_id}/reveal", response_model=RevealSecretResponse)
def reveal_secret(
    secret_id: str,
    payload: RevealSecretRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_scope("secrets:reveal")),
    db: Session = Depends(get_db),
) -> RevealSecretResponse:
    result = secret_reveal.reveal_for_user(
        db,
        secret_id=secret_id,
        user=user,
        passphrase=payload.passphrase,
        ip=get_client_ip(request),
        request=request,
    )
    _no_store(response)
    return RevealSecretResponse(
        content=result.content, views_left=result.views_left, ended=result.ended
    )


@router.post("/{secret_id}/burn", response_model=SecretResponse)
def burn_secret(
    secret_id: str,
    request: Request,
    user: User = Depends(require_scope("secrets:manage")),
    db: Session = Depends(get_db),
) -> SecretResponse:
    secret = _visible_or_404(db, secret_id, user)
    secret_svc.burn_now(db, secret, actor=user, request=request)
    db.commit()
    db.refresh(secret)
    return to_response(db, secret, user)


def _own_or_404(db: Session, secret_id: str, user: User) -> Secret:
    """The sender's own secret. Anyone else who may see it gets a 403; anyone
    who may not, a 404."""
    secret = _visible_or_404(db, secret_id, user)
    if secret.created_by_id != user.id:
        raise AppError(403, "FORBIDDEN", "Only the sender can do that.")
    return secret


@router.get("/{secret_id}/links", response_model=SecretLinksResponse)
def get_links(
    secret_id: str,
    request: Request,
    response: Response,
    user: User = Depends(require_scope("secrets:links")),
    db: Session = Depends(get_db),
) -> SecretLinksResponse:
    secret = _own_or_404(db, secret_id, user)
    rows = secret_svc.links_for_sender(db, secret, actor=user, request=request)
    db.commit()
    _no_store(response)
    return SecretLinksResponse(
        items=[
            SecretLinkItem(
                recipient_id=rec.id,
                kind=rec.kind,
                email=rec.email,
                url=url,
                qr_svg=render_qr_svg(url) if url else None,
            )
            for rec, url in rows
        ]
    )


@router.post("/{secret_id}/link", response_model=SecretLinkResponse)
def replace_link(
    secret_id: str,
    request: Request,
    response: Response,
    user: User = Depends(require_scope("secrets:manage")),
    db: Session = Depends(get_db),
) -> SecretLinkResponse:
    # The answer IS a link, i.e. the secret for anyone who opens it: a token
    # that may manage a secret but not read its links back must not mint one.
    if not request_has_scope(request, "secrets:links"):
        raise AppError(
            403,
            "INSUFFICIENT_SCOPE",
            "This API token lacks the required scope.",
            details={"required_scope": "secrets:links"},
        )
    secret = _own_or_404(db, secret_id, user)
    rec, token = secret_svc.replace_link(db, secret, actor=user, request=request)
    db.commit()
    url = secret_svc.secret_url(db, token)
    _no_store(response)
    return SecretLinkResponse(recipient_id=rec.id, url=url, qr_svg=render_qr_svg(url))


@router.delete("/{secret_id}/link", status_code=status.HTTP_204_NO_CONTENT)
def remove_link(
    secret_id: str,
    request: Request,
    user: User = Depends(require_scope("secrets:manage")),
    db: Session = Depends(get_db),
) -> Response:
    secret = _own_or_404(db, secret_id, user)
    secret_svc.remove_link(db, secret, actor=user, request=request)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
