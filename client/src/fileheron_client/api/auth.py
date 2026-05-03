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
