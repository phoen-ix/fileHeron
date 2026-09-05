"""Adding a passkey is a step-up action.

A passkey whose authenticator verifies the user now satisfies the account's
second factor at login (routers/webauthn.py). Registration used to need only a
live session, which would have let a hijacked session plant a durable TOTP
bypass for anyone who also knew the password. It costs the current password
now - the same gate as /2fa/disable and API-token minting - and it is checked
BEFORE the browser prompt, so a typo never costs the user an authenticator
dialog.
"""
from __future__ import annotations

import pytest

from app.models.user import UserRole

PW = "Pass12345678!"


async def _headers(login_as, email: str) -> dict[str, str]:
    token, _ = await login_as(email, PW)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_register_begin_refuses_a_wrong_password(make_user, client, login_as, monkeypatch):
    from app.services import webauthn as webauthn_svc

    make_user(email="a@test.local", role=UserRole.client, password=PW)
    headers = await _headers(login_as, "a@test.local")

    called: list[int] = []

    async def _fake_begin(db_, *, user):
        called.append(user.id)
        return {"challenge": "c"}

    monkeypatch.setattr(webauthn_svc, "register_begin", _fake_begin)

    r = await client.post(
        "/api/account/webauthn/register/begin",
        json={"password": "not-the-password"},
        headers=headers,
    )

    assert r.status_code == 403, r.text
    assert r.json()["code"] == "INVALID_PASSWORD"
    assert called == [], "the ceremony must not start on a wrong password"


@pytest.mark.asyncio
async def test_register_begin_with_the_right_password_starts_the_ceremony(
    make_user, client, login_as, monkeypatch
):
    from app.services import webauthn as webauthn_svc

    u = make_user(email="a@test.local", role=UserRole.client, password=PW)
    headers = await _headers(login_as, "a@test.local")

    called: list[int] = []

    async def _fake_begin(db_, *, user):
        called.append(user.id)
        return {"challenge": "c"}

    monkeypatch.setattr(webauthn_svc, "register_begin", _fake_begin)

    r = await client.post(
        "/api/account/webauthn/register/begin",
        json={"password": PW},
        headers=headers,
    )

    assert r.status_code == 200, r.text
    assert r.json()["options"] == {"challenge": "c"}
    assert called == [u.id]


@pytest.mark.asyncio
async def test_register_begin_without_a_body_is_a_422_not_a_ceremony(
    make_user, client, login_as, monkeypatch
):
    """An older SPA bundle that still posts an empty body must be refused at
    the schema boundary, not waved through."""
    from app.services import webauthn as webauthn_svc

    make_user(email="a@test.local", role=UserRole.client, password=PW)
    headers = await _headers(login_as, "a@test.local")

    async def _fake_begin(db_, *, user):  # pragma: no cover - must not run
        raise AssertionError("ceremony started without a password")

    monkeypatch.setattr(webauthn_svc, "register_begin", _fake_begin)

    r = await client.post("/api/account/webauthn/register/begin", headers=headers)
    assert r.status_code == 422, r.text
