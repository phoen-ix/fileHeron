"""/api/secret-requests/* - ask someone for a secret, and answer a request (v2.24.0).

No route here returns a secret's text: an answer becomes an ordinary secret for
the requester, opened through `/api/secrets/{id}/reveal`. See
services/secret_request.py.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from ..dependencies import get_db, require_scope
from ..middleware.errors import AppError
from ..models.group import Group
from ..models.secret import SecretRecipientKind
from ..models.secret_request import SecretRequest, SecretRequestState, SecretRequestTarget
from ..models.user import User
from ..schemas.secret import SecretGroupRef, SecretRecipientSummary
from ..schemas.secret_request import (
    AnswerSecretRequestRequest,
    AnswerSecretRequestResponse,
    CreateSecretRequestRequest,
    SecretRequestLinkItem,
    SecretRequestLinksResponse,
    SecretRequestListItem,
    SecretRequestListResponse,
    SecretRequestResponse,
    SecretRequestTargetStatus,
)
from ..services import rate_limit as rate_limit_svc
from ..services import secret_request as request_svc
from ..utils.qr import render_qr_svg
from .secrets import _no_store, _user_ref

router = APIRouter(prefix="/api/secret-requests", tags=["secrets"])

# Per requester, the secret-create figure: each request may mail arbitrary
# addresses, so a compromised account is bounded the same way.
_CREATE_LIMIT = 60
_CREATE_WINDOW_SEC = 900

Role = Literal["requester", "target", "admin"]


def _targets(db: Session, req: SecretRequest) -> list[SecretRequestTarget]:
    return (
        db.query(SecretRequestTarget)
        .filter(SecretRequestTarget.request_id == req.id)
        .order_by(SecretRequestTarget.id)
        .all()
    )


def _summary(targets: list[SecretRequestTarget]) -> SecretRecipientSummary:
    s = request_svc.summary(targets)
    return SecretRecipientSummary(
        users=int(s["users"]), groups=int(s["groups"]), emails=int(s["emails"]), link=bool(s["link"])
    )


def _target_statuses(
    db: Session, targets: list[SecretRequestTarget]
) -> list[SecretRequestTargetStatus]:
    uids = {t.target_user_id for t in targets if t.target_user_id is not None}
    gids = {t.target_group_id for t in targets if t.target_group_id is not None}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(uids))} if uids else {}
    groups = {g.id: g for g in db.query(Group).filter(Group.id.in_(gids))} if gids else {}
    out = []
    for t in targets:
        row = SecretRequestTargetStatus(
            id=t.id, kind=t.kind, email=t.email, notified_at=t.notified_at
        )
        if t.target_user_id is not None:
            row.user = _user_ref(users.get(t.target_user_id))
        if t.target_group_id is not None:
            g = groups.get(t.target_group_id)
            row.group = SecretGroupRef(id=g.id, name=g.name) if g is not None else None
        out.append(row)
    return out


def answered_via(db: Session, req: SecretRequest) -> tuple[
    Literal["user", "email", "link"] | None, SecretRequestTarget | None
]:
    if req.fulfilled_via_target_id is None:
        return None, None
    target = db.get(SecretRequestTarget, req.fulfilled_via_target_id)
    if target is None:
        return None, None
    if target.kind in (SecretRecipientKind.user, SecretRecipientKind.group):
        return "user", target
    return ("email" if target.kind == SecretRecipientKind.email else "link"), target


def to_response(db: Session, req: SecretRequest, viewer: User, role: Role) -> SecretRequestResponse:
    reason = request_svc.closed_reason(req)
    resp = SecretRequestResponse(
        id=req.id,
        state=req.state,
        closed_reason=reason,
        label=req.label,
        note=req.note,
        requester=_user_ref(req.requester),
        created_at=req.created_at,
        expires_at=req.expires_at,
        ended_at=req.ended_at,
        answer_max_views=req.answer_max_views,
        answer_expires_in_sec=req.answer_expires_in_sec,
        has_passphrase=req.public_key is not None,
        viewer_role=role,
    )
    if role == "target":
        resp.can_answer = reason is None
        return resp
    targets = _targets(db, req)
    resp.targets = _target_statuses(db, targets)
    resp.target_summary = _summary(targets)
    resp.fulfilled_at = req.fulfilled_at
    via, target = answered_via(db, req)
    resp.answered_via = via
    if req.fulfilled_by_user_id is not None:
        resp.answered_by = _user_ref(db.get(User, req.fulfilled_by_user_id))
    if role == "requester":
        if target is not None and target.kind == SecretRecipientKind.email:
            resp.answered_by_email = target.email
        answer = request_svc.answer_of(db, req)
        resp.answer_secret_id = answer.id if answer is not None else None
    return resp


def _visible_or_404(db: Session, request_id: str, user: User) -> tuple[SecretRequest, Role]:
    req = request_svc.get_or_404(db, request_id)
    role = request_svc.viewer_role(db, req, user)
    if role is None:
        raise AppError(404, "SECRET_REQUEST_NOT_FOUND", "This request does not exist.")
    return req, role  # type: ignore[return-value]


@router.post("", response_model=SecretRequestResponse, status_code=status.HTTP_201_CREATED)
def create_request(
    payload: CreateSecretRequestRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_scope("secrets:request")),
    db: Session = Depends(get_db),
) -> SecretRequestResponse:
    if not rate_limit_svc.check_ip_allowed(
        "secret_request_create", f"u{user.id}", _CREATE_LIMIT, _CREATE_WINDOW_SEC
    ):
        raise AppError(
            429, "RATE_LIMITED", "You're asking for secrets too quickly; try again shortly."
        )
    created = request_svc.create_request(
        db,
        requester=user,
        label=payload.label,
        note=payload.note,
        expires_at=payload.expires_at,
        answer_max_views=payload.answer_max_views,
        answer_expires_in_sec=payload.answer_expires_in_sec,
        passphrase=payload.passphrase,
        user_ids=payload.recipients.user_ids,
        group_ids=payload.recipients.group_ids,
        emails=payload.recipients.emails,
        create_link=payload.create_link,
        request=request,
    )
    db.commit()
    db.refresh(created.request)
    resp = to_response(db, created.request, user, "requester")
    if created.link_token is not None:
        url = request_svc.request_url(db, created.link_token)
        resp.link_url = url
        resp.link_qr_svg = render_qr_svg(url)
        _no_store(response)
    return resp


@router.get("", response_model=SecretRequestListResponse)
def list_requests(
    box: str = Query("mine", pattern="^(mine|asked)$"),
    state: list[SecretRequestState] = Query(default_factory=list),  # noqa: B008
    q: str = Query("", max_length=200),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
    user: User = Depends(require_scope("secrets:request")),
    db: Session = Depends(get_db),
) -> SecretRequestListResponse:
    rows, total = request_svc.list_for_user(
        db, user=user, box=box, states=state or None, q=q, page=page, page_size=page_size
    )
    items = []
    for req in rows:
        reason = request_svc.closed_reason(req)
        item = SecretRequestListItem(
            id=req.id,
            state=req.state,
            closed_reason=reason,
            label=req.label,
            requester=_user_ref(req.requester),
            created_at=req.created_at,
            expires_at=req.expires_at,
            ended_at=req.ended_at,
            has_passphrase=req.public_key is not None,
        )
        if box == "mine":
            item.target_summary = _summary(_targets(db, req))
            answer = request_svc.answer_of(db, req)
            item.answer_secret_id = answer.id if answer is not None else None
        else:
            item.can_answer = reason is None
        items.append(item)
    return SecretRequestListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{request_id}", response_model=SecretRequestResponse)
def get_request(
    request_id: str,
    user: User = Depends(require_scope("secrets:request")),
    db: Session = Depends(get_db),
) -> SecretRequestResponse:
    req, role = _visible_or_404(db, request_id, user)
    return to_response(db, req, user, role)


@router.post("/{request_id}/answer", response_model=AnswerSecretRequestResponse)
def answer_request(
    request_id: str,
    payload: AnswerSecretRequestRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_scope("secrets:send")),
    db: Session = Depends(get_db),
) -> AnswerSecretRequestResponse:
    request_svc.answer_request(
        db,
        request_id=request_id,
        content=payload.content,
        passphrase=payload.passphrase,
        by_user=user,
        request=request,
    )
    db.commit()
    req = request_svc.get_or_404(db, request_id)
    _no_store(response)
    return AnswerSecretRequestResponse(ok=True, requester_name=req.requester.display_name)


@router.post("/{request_id}/cancel", response_model=SecretRequestResponse)
def cancel_request(
    request_id: str,
    request: Request,
    user: User = Depends(require_scope("secrets:request")),
    db: Session = Depends(get_db),
) -> SecretRequestResponse:
    req, role = _visible_or_404(db, request_id, user)
    request_svc.cancel_request(db, req, actor=user, request=request)
    db.commit()
    db.refresh(req)
    return to_response(db, req, user, role)


@router.get("/{request_id}/links", response_model=SecretRequestLinksResponse)
def get_links(
    request_id: str,
    request: Request,
    response: Response,
    user: User = Depends(require_scope("secrets:request")),
    db: Session = Depends(get_db),
) -> SecretRequestLinksResponse:
    req, _role = _visible_or_404(db, request_id, user)
    rows = request_svc.links_for_requester(db, req, actor=user, request=request)
    db.commit()
    _no_store(response)
    return SecretRequestLinksResponse(
        items=[
            SecretRequestLinkItem(
                target_id=t.id,
                kind=t.kind,
                email=t.email,
                url=url,
                qr_svg=render_qr_svg(url) if url else None,
            )
            for t, url in rows
        ]
    )
