"""Anonymous answers to a secret request (v2.24.0): the page behind a mailed or
copied request link, `{site}/r#<token>`.

The token lives in the URL FRAGMENT - no proxy, access log or Referer sees it -
and the SPA posts it in the request BODY. Both routes are POSTs: mail gateways
open links before people do; `peek` changes nothing, and only `answer` (an
explicit submit) does. One answer per request; every token closes with it.

Mounted under `/api/public/`, which the scan guard never counts.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ..dependencies import get_db
from ..middleware.errors import AppError
from ..schemas.secret import PublicSecretTokenRequest
from ..schemas.secret_request import (
    AnswerSecretRequestResponse,
    PublicAnswerSecretRequestRequest,
    PublicSecretRequestPeekResponse,
)
from ..services import rate_limit as rate_limit_svc
from ..services import secret_request as request_svc
from ..utils.client_ip import get_client_ip
from .secrets import _no_store

router = APIRouter(prefix="/api/public/secret-requests", tags=["public"])

# Per source, across all requests - the public secret figure.
_PUBLIC_LIMIT = 60
_PUBLIC_WINDOW_SEC = 60


def _throttle(request: Request) -> None:
    ip = get_client_ip(request)
    if not rate_limit_svc.check_ip_allowed(
        "secret_request_public", ip or "unknown", _PUBLIC_LIMIT, _PUBLIC_WINDOW_SEC
    ):
        raise AppError(429, "RATE_LIMITED", "Too many requests; try again shortly.")


@router.post("/peek", response_model=PublicSecretRequestPeekResponse)
def peek(
    payload: PublicSecretTokenRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> PublicSecretRequestPeekResponse:
    _throttle(request)
    result = request_svc.peek(db, payload.token)
    req = result.request
    _no_store(response)
    return PublicSecretRequestPeekResponse(
        requester_name=result.requester_name,
        label=req.label,
        note=req.note,
        expires_at=req.expires_at,
        answer_max_views=req.answer_max_views,
        answer_expires_in_sec=req.answer_expires_in_sec,
        has_passphrase=req.public_key is not None,
    )


@router.post("/answer", response_model=AnswerSecretRequestResponse)
def answer(
    payload: PublicAnswerSecretRequestRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AnswerSecretRequestResponse:
    _throttle(request)
    target = request_svc.target_by_token(db, payload.token)
    request_svc.answer_request(
        db,
        request_id=target.request_id,
        content=payload.content,
        passphrase=payload.passphrase,
        by_token=payload.token,
        request=request,
    )
    db.commit()
    req = request_svc.get_or_404(db, target.request_id)
    _no_store(response)
    return AnswerSecretRequestResponse(
        ok=True, requester_name=req.requester.display_name if req.requester else None
    )
