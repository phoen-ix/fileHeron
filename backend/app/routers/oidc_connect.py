"""Authed OIDC connect flow - `/api/account/oidc/...` (Phase 10).

Lets a user who already has a fileHeron account (via password +
optional 2FA) bind their account to one of the configured OIDC
providers. Refuses if the IdP-asserted email doesn't match the
authed user's email.

Endpoints:
- ``POST /api/account/oidc/connect/start/{provider_id}`` →
  returns ``{redirect_url}`` and sets a state cookie carrying
  ``{state}::{provider_id}::{actor_user_id}`` so the callback can
  verify the same browser owns both ends of the round-trip.
- ``GET  /api/account/oidc/connect/callback/{provider_id}`` →
  IdP returns here. Validates state, validates the cookie's actor
  matches the currently-authed user, and hands off to
  ``handle_connect_callback``.
- ``GET    /api/account/oidc/links`` - current user's link (0 or 1).
- ``DELETE /api/account/oidc/links`` - unlink.

The callback validates the actor with ONE check: the signed state cookie
carries the user_id that initiated the round-trip, and that id is what the
binding is applied to.

This docstring used to describe a second check - that the callback requires an
authenticated session and cross-checks `cookie_user_id == authed.id` - and
called the pair defense-in-depth. That check has never existed: the callback is
a top-level browser navigation returning from the IdP, so it carries no
Authorization header and takes no user dependency. Describing a guard that is
not there is worse than having one guard, because the next person to touch this
reads the docstring and assumes the cookie is belt-and-braces rather than the
whole belt (audit 2026-07-30).

What actually protects the flow: the cookie is HMAC-signed, single-use, and
scoped to the callback path, so an attacker cannot forge the actor id, and the
IdP-asserted email must still match the target user's before a bind is written.
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import logging

from fastapi import APIRouter, Cookie, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..dependencies import get_current_user, get_db
from ..middleware.errors import AppError
from ..models.oidc_provider import OIDCProvider
from ..models.user import User
from ..schemas.common import OkResponse
from ..schemas.oidc_connect import (
    ConnectStartResponse,
    OIDCLinkItem,
    OIDCLinkResponse,
)
from ..services import oidc as oidc_svc
from ..services import oidc_admin as oidc_admin_svc
from ..utils.crypto import constant_time_equals

logger = logging.getLogger("fileheron.routers.oidc_connect")

# The authed connect flow: /connect/start + /links + unlink are gated (need the
# Bearer session). The callback is on a SEPARATE, UNGATED router: the IdP returns
# via a top-level browser navigation that carries only cookies, so it can't satisfy
# the Bearer gate - the signed state cookie authenticates the actor instead.
router = APIRouter(prefix="/api/account/oidc", tags=["oidc-connect"])
callback_router = APIRouter(prefix="/api/account/oidc", tags=["oidc-connect"])

_CONNECT_COOKIE = "fh_oidc_connect_state"
_COOKIE_PATH = "/api/account/oidc"


def _pack(state: str, provider_id: str, user_id: int, nonce: str) -> str:
    # The callback trusts cookie_user_id to authenticate the actor (no Bearer), so
    # the packed value MUST be tamper-proof - HMAC it under JWT_SECRET.
    payload = f"{state}::{provider_id}::{user_id}::{nonce}"
    sig = hmac_mod.new(
        settings.JWT_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{sig}"


def _unpack(
    packed: str | None,
) -> tuple[str | None, str | None, int | None, str | None]:
    if not packed or "." not in packed:
        return None, None, None, None
    payload, _, sig = packed.rpartition(".")
    expected = hmac_mod.new(
        settings.JWT_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not constant_time_equals(expected, sig):
        return None, None, None, None
    parts = payload.split("::")
    if len(parts) != 4:
        return None, None, None, None
    state, provider_id, uid_str, nonce = parts
    try:
        user_id = int(uid_str)
    except ValueError:
        return None, None, None, None
    return state, provider_id, user_id, nonce


def _sub_hint(sub: str) -> str:
    """Display-safe last-4 of the subject. Provider subs are often
    GUIDs we don't want to fully surface."""
    if not sub:
        return ""
    if len(sub) <= 4:
        return f"…{sub}"
    return f"…{sub[-4:]}"


def _set_state_cookie(response: Response, packed: str) -> None:
    response.set_cookie(
        key=_CONNECT_COOKIE,
        value=packed,
        max_age=oidc_svc.STATE_TTL_SEC,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path=_COOKIE_PATH,
    )


# ---------------------------------------------------------------------------
# Connect start
# ---------------------------------------------------------------------------


@router.post(
    "/connect/start/{provider_id}", response_model=ConnectStartResponse
)
async def connect_start(
    provider_id: str,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConnectStartResponse:
    if user.oidc_provider_id and user.oidc_provider_id != provider_id:
        raise AppError(
            409,
            "OIDC_ALREADY_LINKED",
            "You're already linked to another OIDC provider - disconnect that first.",
        )
    provider = oidc_admin_svc.get_enabled_provider(db, provider_id)
    url, state, nonce = await oidc_svc.build_authorize_url(
        provider, kind="connect"
    )
    _set_state_cookie(response, _pack(state, provider.id, user.id, nonce))
    return ConnectStartResponse(redirect_url=url)


# ---------------------------------------------------------------------------
# Connect callback
# ---------------------------------------------------------------------------


@callback_router.get("/connect/callback/{provider_id}")
async def connect_callback(
    provider_id: str,
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    fh_oidc_connect_state: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Browser-facing: the IdP redirects the user here, so a failure must land
    back in the SPA rather than on a raw JSON body at an /api/ URL with no way
    back (audit 2026-07-30). The account view already renders `oidc_error`."""
    try:
        return await _connect_callback_inner(
            provider_id, request, code, state, fh_oidc_connect_state, db
        )
    except AppError as e:
        logger.info(
            "oidc connect callback failed provider=%s code=%s", provider_id, e.code
        )
        from ..services import site as site_svc
        return RedirectResponse(
            url=f"{site_svc.get_site_url(db)}/account?oidc_error={e.code}",
            status_code=302,
        )


async def _connect_callback_inner(
    provider_id: str,
    request: Request,
    code: str,
    state: str,
    fh_oidc_connect_state: str | None,
    db: Session,
) -> RedirectResponse:
    # No Bearer here (IdP browser redirect carries only cookies). The actor is the
    # user_id inside the HMAC-signed state cookie - _unpack rejects a forged one.
    cookie_state, cookie_provider_id, cookie_user_id, cookie_nonce = _unpack(
        fh_oidc_connect_state
    )
    if (
        cookie_provider_id != provider_id
        or cookie_user_id is None
        or not cookie_state
        or not cookie_nonce
    ):
        raise AppError(
            401,
            "OIDC_STATE_MISMATCH",
            "OIDC connect state mismatch - please retry.",
        )
    user = db.query(User).filter(User.id == cookie_user_id).one_or_none()
    if user is None or user.is_disabled:
        raise AppError(401, "OIDC_STATE_MISMATCH", "OIDC connect state mismatch - please retry.")

    provider = oidc_admin_svc.get_enabled_provider(db, provider_id)

    await oidc_svc.handle_connect_callback(
        db,
        provider=provider,
        user=user,
        code=code,
        state_cookie=cookie_state,
        state_param=state,
        expected_nonce=cookie_nonce,
        request=request,
    )
    db.commit()

    # Land back in the SPA's account view with a flag so it can
    # surface a success toast and refresh the panel.
    from ..services import site as site_svc
    response = RedirectResponse(
        url=f"{site_svc.get_site_url(db)}/account?oidc_connected=1", status_code=302
    )
    response.delete_cookie(_CONNECT_COOKIE, path=_COOKIE_PATH)
    return response


# ---------------------------------------------------------------------------
# Link inspection / unlink
# ---------------------------------------------------------------------------


def _link_item(provider: OIDCProvider, sub: str) -> OIDCLinkItem:
    return OIDCLinkItem(
        provider_id=provider.id,
        provider_name=provider.name,
        preset=provider.preset,
        sub_hint=_sub_hint(sub),
    )


@router.get("/links", response_model=OIDCLinkResponse)
def get_links(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OIDCLinkResponse:
    if not user.oidc_provider_id or not user.oidc_subject:
        return OIDCLinkResponse(link=None)
    provider = oidc_admin_svc.get_provider_for_user(db, user)
    if provider is None:
        # Race: provider was deleted with ON DELETE SET NULL elsewhere.
        return OIDCLinkResponse(link=None)
    return OIDCLinkResponse(link=_link_item(provider, user.oidc_subject))


@router.delete("/links", response_model=OkResponse)
def delete_link(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    oidc_svc.unlink(db, user=user, request=request)
    db.commit()
    return {"ok": True}
