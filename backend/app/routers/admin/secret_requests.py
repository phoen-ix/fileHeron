"""Admin view of secret requests (v2.24.0): metadata only, plus Cancel.

An admin sees who asked whom for what and the state; they never see a link or
an answer's text (the answer is a secret, readable by the requester alone).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...models.secret_request import SecretRequestState
from ...models.user import User
from ...schemas.secret_request import (
    AdminSecretRequestListItem,
    AdminSecretRequestListResponse,
    SecretRequestResponse,
)
from ...services import secret_request as request_svc
from ..secret_requests import _summary, _targets, to_response
from ..secrets import _user_ref

router = APIRouter()


@router.get("/secret-requests", response_model=AdminSecretRequestListResponse)
def list_requests(
    state: list[SecretRequestState] = Query(default_factory=list),  # noqa: B008
    q: str = Query("", max_length=200),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> AdminSecretRequestListResponse:
    rows, total = request_svc.list_all(
        db, states=state or None, q=q, page=page, page_size=page_size
    )
    items = [
        AdminSecretRequestListItem(
            id=req.id,
            state=req.state,
            closed_reason=request_svc.closed_reason(req),
            label=req.label,
            requester=_user_ref(req.requester),
            requester_email=req.requester.email,
            created_at=req.created_at,
            expires_at=req.expires_at,
            ended_at=req.ended_at,
            has_passphrase=req.public_key is not None,
            target_summary=_summary(_targets(db, req)),
        )
        for req in rows
    ]
    return AdminSecretRequestListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/secret-requests/{request_id}/cancel", response_model=SecretRequestResponse)
def cancel_request(
    request_id: str,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> SecretRequestResponse:
    req = request_svc.get_or_404(db, request_id)
    request_svc.cancel_request(db, req, actor=admin, request=request)
    db.commit()
    db.refresh(req)
    return to_response(db, req, admin, "admin")
