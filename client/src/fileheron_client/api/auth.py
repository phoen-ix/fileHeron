"""Auth endpoints.

The desktop client hits the same ``/api/auth/{login,refresh,logout}``
+ ``/api/account/me`` routes the SPA does. The refresh cookie
(``fh_refresh``, path-scoped to ``/api/auth``) is held in the
``httpx.Client`` cookie jar inside ``ApiClient``."""
from __future__ import annotations

from typing import Optional

from .client import ApiClient, _envelope_from_response
from ..models import LoginResponse, MeResponse, RefreshResponse


def login(
    api: ApiClient, *, email: str, password: str, totp_code: Optional[str] = None
) -> LoginResponse:
    body = {"email": email, "password": password}
    if totp_code:
        body["totp_code"] = totp_code
    resp = api.request("POST", "/api/auth/login", json=body, retry_on_401=False)
    if resp.status_code != 200:
        raise _envelope_from_response(resp)
    out = LoginResponse.model_validate(resp.json())
    api.set_access_token(out.access_token)
    return out


def login_with_recovery(
    api: ApiClient, *, email: str, password: str, recovery_code: str
) -> LoginResponse:
    """v0.7.0: sign in using a one-time recovery code instead of TOTP.

    Backend route ``POST /api/auth/login/recovery`` consumes one recovery
    code per call (single-use; the user has 10 from 2FA enrolment). Same
    LoginResponse shape as ``login()``. Error envelope codes the user
    surface should know about:

    - ``INVALID_RECOVERY`` (401) — code is wrong or already used
    - ``INVALID_CREDENTIALS`` (401) — wrong email/password
    - ``RATE_LIMITED`` (429) — IP exceeded the sliding window
    - ``ACCOUNT_LOCKED`` (401) — soft lockout from too many failures
    """
    body = {
        "email": email,
        "password": password,
        "recovery_code": recovery_code,
    }
    resp = api.request(
        "POST", "/api/auth/login/recovery", json=body, retry_on_401=False,
    )
    if resp.status_code != 200:
        raise _envelope_from_response(resp)
    out = LoginResponse.model_validate(resp.json())
    api.set_access_token(out.access_token)
    return out


def refresh(api: ApiClient) -> RefreshResponse:
    out = api.request_or_raise("POST", "/api/auth/refresh")
    parsed = RefreshResponse.model_validate(out)
    api.set_access_token(parsed.access_token)
    return parsed


def logout(api: ApiClient) -> None:
    # Best-effort — server clears the cookie regardless.
    api.request("POST", "/api/auth/logout", retry_on_401=False)
    api.set_access_token(None)


def me(api: ApiClient) -> MeResponse:
    out = api.request_or_raise("GET", "/api/account/me")
    return MeResponse.model_validate(out)
