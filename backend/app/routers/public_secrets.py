"""Anonymous secret reveal (v2.24.0): the page behind an emailed or copied link.

The link is `{site}/s#<token>`. The token lives in the URL FRAGMENT, which a
browser never sends, so it reaches no proxy, no access log and no Referer - a
path token was once found verbatim in a world-readable Traefik log. The SPA
reads the fragment and posts the token in the request BODY, here.

Both routes are POSTs on purpose. Mail gateways (SafeLinks, Proofpoint,
Mimecast) open links before the recipient does, and some run the page's script;
`peek` costs nothing, and only `reveal` - an explicit click - uses a view.

Mounted under `/api/public/`, which the scan guard never counts: a revoked link
answering 404 to a gateway's egress pool is not a scan.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ..dependencies import get_db
from ..middleware.errors import AppError
from ..schemas.secret import (
    PublicRevealSecretRequest,
    PublicSecretPeekResponse,
    PublicSecretTokenRequest,
    RevealSecretResponse,
)
from ..services import rate_limit as rate_limit_svc
from ..services import secret_reveal
from ..utils.client_ip import get_client_ip
from .secrets import _no_store

router = APIRouter(prefix="/api/public/secrets", tags=["public"])

# Per source, across all links: well above a person retrying, and a bound on
# anyone spending Argon2 derivations against random tokens.
_PUBLIC_LIMIT = 60
_PUBLIC_WINDOW_SEC = 60


def _throttle(request: Request) -> str | None:
    ip = get_client_ip(request)
    if not rate_limit_svc.check_ip_allowed(
        "secret_public", ip or "unknown", _PUBLIC_LIMIT, _PUBLIC_WINDOW_SEC
    ):
        raise AppError(429, "RATE_LIMITED", "Too many requests; try again shortly.")
    return ip


@router.post("/peek", response_model=PublicSecretPeekResponse)
def peek(
    payload: PublicSecretTokenRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> PublicSecretPeekResponse:
    _throttle(request)
    result = secret_reveal.peek(db, payload.token)
    _no_store(response)
    return PublicSecretPeekResponse(
        requires_passphrase=result.requires_passphrase,
        label=result.label,
        sender_name=result.sender_name,
        expires_at=result.expires_at,
        views_left=result.views_left,
        locked_until=result.locked_until,
        attempts_left=result.attempts_left,
    )


@router.post("/reveal", response_model=RevealSecretResponse)
def reveal(
    payload: PublicRevealSecretRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> RevealSecretResponse:
    ip = _throttle(request)
    result = secret_reveal.reveal_by_token(
        db, token=payload.token, passphrase=payload.passphrase, ip=ip, request=request
    )
    _no_store(response)
    return RevealSecretResponse(
        content=result.content, views_left=result.views_left, ended=result.ended
    )
