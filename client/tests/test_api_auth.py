"""Auth API: login (with TOTP variant), refresh, me, 401 retry."""
from __future__ import annotations

import httpx
import pytest
import respx

from fileheron_client.api import ApiClient, ApiError, SessionExpiredError
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
def test_login_non_json_200_raises_clean_apierror():
    """Finding C3: a 200 with a non-JSON body must surface as a clean
    ApiError(MALFORMED_RESPONSE), not a raw ValueError."""
    respx.post(f"{SERVER}/api/auth/login").mock(
        return_value=httpx.Response(200, text="<html>gateway</html>")
    )
    api = ApiClient(SERVER)
    with pytest.raises(ApiError) as ei:
        auth_api.login(api, email="a@b.c", password="pw")
    assert ei.value.code == "MALFORMED_RESPONSE"


@respx.mock
def test_me_non_json_200_raises_clean_apierror():
    respx.get(f"{SERVER}/api/account/me").mock(
        return_value=httpx.Response(200, text="not json")
    )
    api = ApiClient(SERVER, access_token="ACCESS")
    with pytest.raises(ApiError) as ei:
        auth_api.me(api)
    assert ei.value.code == "MALFORMED_RESPONSE"


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
            # Backend retired the HMAC + email_hint design - see CLAUDE.md
            # "Email storage": plaintext in users.email so notification
            # dispatchers can address them. MeResponse expects 'email'.
            "id": 1,
            "email": "a@b.c",
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
def test_401_after_failed_refresh_raises_session_expired():
    """v0.9.1: when a 401 can't be recovered by a refresh, the client raises
    SessionExpiredError (a subclass of ApiError) so the UI's async layer can
    bounce the user back to the login overlay instead of rendering an inline
    error on a dead screen."""
    respx.get(f"{SERVER}/api/account/me").mock(
        return_value=httpx.Response(401, json={"code": "TOKEN_EXPIRED", "error": "expired"})
    )
    respx.post(f"{SERVER}/api/auth/refresh").mock(
        return_value=httpx.Response(401, json={"code": "REFRESH_INVALID", "error": "bad"})
    )
    api = ApiClient(SERVER, access_token="OLD")
    with pytest.raises(SessionExpiredError) as ei:
        auth_api.me(api)
    assert isinstance(ei.value, ApiError)  # still matches existing panel checks
    assert ei.value.status_code == 401
    assert ei.value.code == "SESSION_EXPIRED"


@respx.mock
def test_api_token_path_does_not_attempt_refresh():
    """A 401 with an API token should NOT call /api/auth/refresh - API
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


# v0.7.0 recovery-code login --------------------------------------------------


@respx.mock
def test_login_with_recovery_happy_path():
    captured = {}

    def _on_call(request: httpx.Request) -> httpx.Response:
        import json as _j

        captured.update(_j.loads(request.content))
        return httpx.Response(
            200, json={"access_token": "ACCESS", "expires_in_seconds": 900}
        )

    respx.post(f"{SERVER}/api/auth/login/recovery").mock(side_effect=_on_call)
    api = ApiClient(SERVER)
    out = auth_api.login_with_recovery(
        api, email="a@b.c", password="pw", recovery_code="abc123def456",
    )
    assert out.access_token == "ACCESS"
    assert api.access_token == "ACCESS"
    assert captured == {
        "email": "a@b.c",
        "password": "pw",
        "recovery_code": "abc123def456",
    }


@respx.mock
def test_login_with_recovery_invalid_code_raises_envelope():
    respx.post(f"{SERVER}/api/auth/login/recovery").mock(
        return_value=httpx.Response(
            401,
            json={
                "error": "Recovery code is invalid or already used.",
                "code": "INVALID_RECOVERY",
                "details": {},
                "request_id": "abc",
            },
        )
    )
    api = ApiClient(SERVER)
    with pytest.raises(ApiError) as ei:
        auth_api.login_with_recovery(
            api, email="a@b.c", password="pw", recovery_code="bad-code",
        )
    assert ei.value.code == "INVALID_RECOVERY"
    assert ei.value.status_code == 401


@respx.mock
def test_login_with_recovery_rate_limited_propagates():
    respx.post(f"{SERVER}/api/auth/login/recovery").mock(
        return_value=httpx.Response(
            429,
            json={
                "error": "Too many login attempts.",
                "code": "RATE_LIMITED",
                "details": {},
                "request_id": "xyz",
            },
        )
    )
    api = ApiClient(SERVER)
    with pytest.raises(ApiError) as ei:
        auth_api.login_with_recovery(
            api, email="a@b.c", password="pw", recovery_code="abc123def456",
        )
    assert ei.value.code == "RATE_LIMITED"
    assert ei.value.status_code == 429
