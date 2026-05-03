"""TOTP / 2FA flow: setup, enable, login challenge, recovery codes, disable."""
from __future__ import annotations

import pyotp
import pytest


async def _login(client, email: str, password: str, totp_code: str | None = None):
    body = {"email": email, "password": password}
    if totp_code is not None:
        body["totp_code"] = totp_code
    return await client.post("/api/auth/login", json=body)


async def _enable_totp(client, email: str, password: str) -> tuple[str, list[str]]:
    """Helper: log in, run the full 2FA setup flow, return (secret_b32, recovery_codes)."""
    login = await _login(client, email, password)
    assert login.status_code == 200, login.text
    access = login.json()["access_token"]

    setup = await client.post(
        "/api/account/2fa/setup", headers={"Authorization": f"Bearer {access}"}
    )
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret_b32"]

    code = pyotp.TOTP(secret).now()
    enable = await client.post(
        "/api/account/2fa/enable",
        headers={"Authorization": f"Bearer {access}"},
        json={"code": code},
    )
    assert enable.status_code == 200, enable.text
    return secret, enable.json()["recovery_codes"]


@pytest.mark.asyncio
async def test_setup_returns_secret_uri_and_qr(make_user, client):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")
    login = await _login(client, "alice@test.local", "LongCorrectHorse123!")
    access = login.json()["access_token"]

    setup = await client.post(
        "/api/account/2fa/setup", headers={"Authorization": f"Bearer {access}"}
    )
    assert setup.status_code == 200
    body = setup.json()
    assert len(body["secret_b32"]) >= 16
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert body["qr_svg"].startswith("<?xml version=") or body["qr_svg"].startswith("<svg")


@pytest.mark.asyncio
async def test_enable_with_invalid_code_fails(make_user, client):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")
    login = await _login(client, "alice@test.local", "LongCorrectHorse123!")
    access = login.json()["access_token"]

    await client.post("/api/account/2fa/setup", headers={"Authorization": f"Bearer {access}"})
    bad = await client.post(
        "/api/account/2fa/enable",
        headers={"Authorization": f"Bearer {access}"},
        json={"code": "000000"},
    )
    assert bad.status_code == 401
    assert bad.json()["code"] == "INVALID_TOTP"


@pytest.mark.asyncio
async def test_enable_returns_ten_recovery_codes(make_user, client):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")
    secret, codes = await _enable_totp(client, "alice@test.local", "LongCorrectHorse123!")
    assert len(codes) == 10
    assert all("-" in c and len(c) == 9 for c in codes)
    assert len(set(codes)) == 10  # all unique


@pytest.mark.asyncio
async def test_login_requires_totp_after_enable(make_user, client):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")
    await _enable_totp(client, "alice@test.local", "LongCorrectHorse123!")

    # Plain login without totp_code is now blocked.
    resp = await _login(client, "alice@test.local", "LongCorrectHorse123!")
    assert resp.status_code == 401
    assert resp.json()["code"] == "TOTP_REQUIRED"


@pytest.mark.asyncio
async def test_login_with_valid_totp_succeeds(make_user, client):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")
    secret, _ = await _enable_totp(client, "alice@test.local", "LongCorrectHorse123!")

    code = pyotp.TOTP(secret).now()
    resp = await _login(client, "alice@test.local", "LongCorrectHorse123!", totp_code=code)
    assert resp.status_code == 200
    assert resp.json()["access_token"]


@pytest.mark.asyncio
async def test_login_with_invalid_totp_fails(make_user, client):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")
    await _enable_totp(client, "alice@test.local", "LongCorrectHorse123!")

    resp = await _login(client, "alice@test.local", "LongCorrectHorse123!", totp_code="000000")
    assert resp.status_code == 401
    assert resp.json()["code"] == "INVALID_TOTP"


@pytest.mark.asyncio
async def test_login_with_recovery_code_succeeds_and_consumes(make_user, client):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")
    _secret, codes = await _enable_totp(client, "alice@test.local", "LongCorrectHorse123!")
    code = codes[0]

    first = await client.post(
        "/api/auth/login/recovery",
        json={"email": "alice@test.local", "password": "LongCorrectHorse123!", "recovery_code": code},
    )
    assert first.status_code == 200, first.text

    # Re-using the same code must fail.
    second = await client.post(
        "/api/auth/login/recovery",
        json={"email": "alice@test.local", "password": "LongCorrectHorse123!", "recovery_code": code},
    )
    assert second.status_code == 401
    assert second.json()["code"] == "INVALID_RECOVERY"


@pytest.mark.asyncio
async def test_disable_requires_password_and_code(make_user, client):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")
    secret, codes = await _enable_totp(client, "alice@test.local", "LongCorrectHorse123!")

    # Login again with TOTP to get a fresh access token.
    login = await _login(
        client, "alice@test.local", "LongCorrectHorse123!", totp_code=pyotp.TOTP(secret).now()
    )
    access = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}

    # Wrong password.
    bad_pw = await client.post(
        "/api/account/2fa/disable",
        headers=headers,
        json={"password": "wrong", "code_or_recovery": codes[0]},
    )
    assert bad_pw.status_code == 401
    assert bad_pw.json()["code"] == "INVALID_CREDENTIALS"

    # Right password + recovery code: works.
    good = await client.post(
        "/api/account/2fa/disable",
        headers=headers,
        json={"password": "LongCorrectHorse123!", "code_or_recovery": codes[0]},
    )
    assert good.status_code == 200, good.text

    # Now login without TOTP works again.
    plain = await _login(client, "alice@test.local", "LongCorrectHorse123!")
    assert plain.status_code == 200


@pytest.mark.asyncio
async def test_status_endpoint_reports_correctly(make_user, client):
    make_user(email="alice@test.local", password="LongCorrectHorse123!")
    login = await _login(client, "alice@test.local", "LongCorrectHorse123!")
    access = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}

    s1 = await client.get("/api/account/2fa/status", headers=headers)
    assert s1.status_code == 200
    assert s1.json() == {"enabled": False, "enabled_at": None, "recovery_codes_remaining": 0}

    await _enable_totp(client, "alice@test.local", "LongCorrectHorse123!")
    s2 = await client.get("/api/account/2fa/status", headers=headers)
    assert s2.status_code == 200
    body = s2.json()
    assert body["enabled"] is True
    assert body["enabled_at"] is not None
    assert body["recovery_codes_remaining"] == 10
