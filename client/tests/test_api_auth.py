"""Auth API: login (with TOTP variant), refresh, me, 401 retry."""
from __future__ import annotations

import httpx
import pytest
import respx

from fileheron_client.api import ApiClient, ApiError
from fileheron_client.api import auth as auth_api


SERVER = "https://files.example.com"


@respx.mock
def test_login_happy_path():
    respx.post(f"{SERVER}/api/auth/login").mock(
        return_value=httpx.Response(
            200, json={"access_token": "ACCESS", "expires_in_seconds": 900}
        )
    )
    api = ApiClient(SERVER)
    out = auth_api.login(api, email="a@b.c", password="pw")
    assert out.access_token == "ACCESS"
    assert api.access_token == "ACCESS"


@respx.mock
def test_login_totp_required_raises_envelope():
    respx.post(f"{SERVER}/api/auth/login").mock(
        return_value=httpx.Response(
            401,
            json={
                "error": "TOTP code required",
                "code": "TOTP_REQUIRED",
                "details": {},
                "request_id": "abc",
            },
        )
    )
    api = ApiClient(SERVER)
    with pytest.raises(ApiError) as ei:
        auth_api.login(api, email="a@b.c", password="pw")
    assert ei.value.code == "TOTP_REQUIRED"
    assert ei.value.status_code == 401


@respx.mock
def test_login_passes_totp_when_provided():
    captured = {}

    def _on_call(request: httpx.Request) -> httpx.Response:
        import json as _j

        captured.update(_j.loads(request.content))
        return httpx.Response(200, json={"access_token": "X", "expires_in_seconds": 900})

    respx.post(f"{SERVER}/api/auth/login").mock(side_effect=_on_call)
    api = ApiClient(SERVER)
    auth_api.login(api, email="a@b.c", password="pw", totp_code="123456")
    assert captured.get("totp_code") == "123456"


@respx.mock
def test_401_triggers_one_refresh_then_replays():
    """When an access token is set, a 401 from a non-/api/auth/ endpoint
    should trigger exactly one refresh + one replay of the original."""
    me_route = respx.get(f"{SERVER}/api/account/me")
    refresh_route = respx.post(f"{SERVER}/api/auth/refresh")

    me_route.side_effect = [
        httpx.Response(401, json={"code": "TOKEN_EXPIRED", "error": "expired"}),
        httpx.Response(200, json={
            "id": 1,
            "email_hint": "a***@b.c",
            "display_name": "A",
            "role": "client",
            "locale": "en",
        }),
    ]
    refresh_route.return_value = httpx.Response(
        200, json={"access_token": "NEW", "expires_in_seconds": 900}
    )

    api = ApiClient(SERVER, access_token="OLD")
    out = auth_api.me(api)
    assert out.id == 1
    assert api.access_token == "NEW"
    assert refresh_route.call_count == 1
    assert me_route.call_count == 2


@respx.mock
def test_401_after_failed_refresh_propagates():
    respx.get(f"{SERVER}/api/account/me").mock(
        return_value=httpx.Response(401, json={"code": "TOKEN_EXPIRED", "error": "expired"})
    )
    respx.post(f"{SERVER}/api/auth/refresh").mock(
        return_value=httpx.Response(401, json={"code": "REFRESH_INVALID", "error": "bad"})
    )
    api = ApiClient(SERVER, access_token="OLD")
    with pytest.raises(ApiError) as ei:
        auth_api.me(api)
    # Original 401 surfaces (we don't re-retry after refresh fails).
    assert ei.value.status_code == 401


@respx.mock
def test_api_token_path_does_not_attempt_refresh():
    """A 401 with an API token should NOT call /api/auth/refresh — API
    tokens are stable secrets, not session tokens."""
    me_route = respx.get(f"{SERVER}/api/account/me").mock(
        return_value=httpx.Response(401, json={"code": "INVALID_TOKEN", "error": "bad"})
    )
    refresh_route = respx.post(f"{SERVER}/api/auth/refresh")

    api = ApiClient(SERVER, api_token="fh_xxxxxxxx_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    with pytest.raises(ApiError):
        auth_api.me(api)
    assert refresh_route.call_count == 0
    assert me_route.call_count == 1
