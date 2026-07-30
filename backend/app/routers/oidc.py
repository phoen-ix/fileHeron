"""OIDC login endpoints - `/api/auth/oidc/start/{provider_id}` and
`/api/auth/oidc/callback/{provider_id}`.

Browser flow:
1. SPA renders one button per enabled provider on the login page.
2. Click → SPA navigates to `/api/auth/oidc/start/{provider_id}`.
3. Backend validates the provider, redirects 302 to the IdP with a
   short-lived state cookie containing the state nonce + provider_id.
4. IdP authenticates, redirects back to
   `/api/auth/oidc/callback/{provider_id}`.
5. Backend exchanges the code, mints session cookies, redirects home.

Phase 10 changes vs Phase 7/9:
- `provider_id` in path lets us run multiple IdPs concurrently.
- The state cookie stores `{state}::{provider_id}` so a stale cookie
  pointing at provider A can't be replayed against provider B's
  callback.
- Anonymous callback never creates a user - see
  `services/oidc.py::handle_callback`.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Cookie, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..dependencies import get_db
from ..middleware.errors import AppError
from ..services import auth as auth_svc
from ..services import oidc as oidc_svc
from ..services import oidc_admin as oidc_admin_svc
from ..services import rate_limit as rate_limit_svc
from ..services import settings_registry

logger = logging.getLogger("fileheron.routers.oidc")

router = APIRouter(prefix="/api/auth/oidc", tags=["oidc"])


def _pack_state(state: str, provider_id: str, nonce: str) -> str:
    return f"{state}::{provider_id}::{nonce}"


def _unpack_state(
    packed: str | None,
) -> tuple[str | None, str | None, str | None]:
    if not packed:
        return None, None, None
    parts = packed.split("::")
    if len(parts) != 3:
        # Old-format cookie (pre-nonce deploy) - refuse cleanly so the
        # user retries; better than silently skipping nonce check.
        return None, None, None
    state, provider_id, nonce = parts
    return state, provider_id, nonce


@router.get("/start/{provider_id}")
async def start(
    provider_id: str,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    provider = oidc_admin_svc.get_enabled_provider(db, provider_id)
    url, state, nonce = await oidc_svc.build_authorize_url(provider, kind="login")
    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie(
        key=oidc_svc.STATE_COOKIE,
        value=_pack_state(state, provider.id, nonce),
        max_age=oidc_svc.STATE_TTL_SEC,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/api/auth/oidc",
    )
    return response


@router.get("/callback/{provider_id}")
async def callback(
    provider_id: str,
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    fh_oidc_state: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    provider = oidc_admin_svc.get_enabled_provider(db, provider_id)

    cookie_state, cookie_provider_id, cookie_nonce = _unpack_state(fh_oidc_state)
    if cookie_provider_id != provider.id or not cookie_nonce:
        raise AppError(
            401,
            "OIDC_STATE_MISMATCH",
            "OIDC state mismatch - try again.",
        )

    user = await oidc_svc.handle_callback(
        db,
        provider=provider,
        code=code,
        state_cookie=cookie_state,
        state_param=state,
        expected_nonce=cookie_nonce,
        request=request,
    )

    rate_limit_svc.record_success(db, user=user)
    # The access token is deliberately discarded: the browser gets only the
    # httpOnly refresh cookie, and the SPA exchanges it on first paint.
    _access, _expires_in, refresh_plain = auth_svc.finalize_successful_login(
        db, user=user, request=request, settings=settings, via="oidc",
    )
    db.commit()

    from ..services import site as site_svc
    response = RedirectResponse(url=f"{site_svc.get_site_url(db)}/", status_code=302)
    response.set_cookie(
        key="fh_refresh",
        value=refresh_plain,
        max_age=settings_registry.effective(db, settings_registry.K.REFRESH_TOKEN_EXPIRE_DAYS) * 24 * 3600,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/api/auth",
    )
    # No access-token cookie here. There used to be a `fh_oidc_access` cookie
    # carrying the raw JWT with httponly=False and path=/, commented "SPA reads
    # + clears on landing" - but the SPA never read it and never touches
    # document.cookie at all, so it was dead weight that also handed any XSS on
    # the origin a live bearer token for the token's full lifetime (audit
    # 2026-07-30). The SPA cold-loads through auth.bootstrap() -> refreshOnce(),
    # which mints an access token from the httpOnly fh_refresh cookie set above,
    # exactly like every other login flow.
    response.delete_cookie(oidc_svc.STATE_COOKIE, path="/api/auth/oidc")
    # Clear any cookie left in browsers from before that change.
    response.delete_cookie("fh_oidc_access", path="/")
    return response

# (Removed an unauthenticated `_test_reset_discovery` HTTP hook - finding
# L2. Tests clear the cache by calling `oidc_svc.reset_discovery_cache()`
# directly via the conftest autouse fixture, so the endpoint was dead
# weight that let anyone flush the discovery cache repeatedly.)
