"""OIDC login endpoints — `/api/auth/oidc/start/{provider_id}` and
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
- Anonymous callback never creates a user — see
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
from ..services import jwt_session
from ..services import oidc as oidc_svc
from ..services import rate_limit as rate_limit_svc

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
        # Old-format cookie (pre-nonce deploy) — refuse cleanly so the
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
            "OIDC state mismatch — try again.",
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

    access, expires_in = auth_svc.create_access_token(user.id, settings)
    rate_limit_svc.record_success(db, user=user)
    _, refresh_plain = jwt_session.create_refresh_token(db, user, request, settings)
    db.commit()

    from ..services import site as site_svc
    response = RedirectResponse(url=f"{site_svc.get_site_url(db)}/", status_code=302)
    response.set_cookie(
        key="fh_refresh",
        value=refresh_plain,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/api/auth",
    )
    response.set_cookie(
        key="fh_oidc_access",
        value=access,
        max_age=expires_in,
        httponly=False,  # SPA reads + clears on landing
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(oidc_svc.STATE_COOKIE, path="/api/auth/oidc")
    return response


@router.post("/_test_reset_discovery", include_in_schema=False)
def _test_reset_discovery() -> dict:
    """Test hook to clear the discovery cache; not in OpenAPI."""
    oidc_svc.reset_discovery_cache()
    return {"ok": True}
