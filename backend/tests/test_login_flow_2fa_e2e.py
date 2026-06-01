"""End-to-end verification of the v0.9.3 two-step login flow.

The SPA (`frontend/src/views/Login.vue`) and the desktop client
(`client/.../ui/login_window.py`) both now:

  1. submit email + password ALONE. If 2FA is on, the server answers
     ``TOTP_REQUIRED`` and the UI reveals one "authentication code" field.
  2. That single field accepts a 6-digit TOTP code OR a recovery code
     (``XXXX-XXXX``) and routes itself — 6 digits → ``/api/auth/login``,
     anything else → ``/api/auth/login/recovery`` — with no toggle.

This test drives that exact sequence against the real backend (TestClient on
isolated in-memory SQLite) with a real synthetic 2FA account, and asserts the
routing decision matches the predicate both frontends ship.
"""
from __future__ import annotations

import re

import pyotp
import pytest

# The EXACT shape predicate both frontends use to route the single code field.
#   SPA:    /^\d{6}$/.test(v.replace(/\s+/g, ''))
#   client: re.compile(r"^\d{6}$")
_TOTP_SHAPE = re.compile(r"^\d{6}$")

EMAIL = "logintest@test.local"
PW = "LongCorrectHorse123!"


def _routes_to_totp(entered: str) -> bool:
    return bool(_TOTP_SHAPE.fullmatch(entered.replace(" ", "")))


async def _frontend_submit(client, email, password, code):
    """Mirror the frontends' onSubmit: step 1 sends no code; the code step
    routes by shape to /login (TOTP) or /login/recovery (recovery)."""
    if code is None:
        return await client.post("/api/auth/login", json={"email": email, "password": password})
    if _routes_to_totp(code):
        return await client.post(
            "/api/auth/login",
            json={"email": email, "password": password, "totp_code": code.replace(" ", "")},
        )
    return await client.post(
        "/api/auth/login/recovery",
        json={"email": email, "password": password, "recovery_code": code},
    )


async def _enable_2fa(client, email, password):
    login = await client.post("/api/auth/login", json={"email": email, "password": password})
    access = login.json()["access_token"]
    setup = await client.post("/api/account/2fa/setup", headers={"Authorization": f"Bearer {access}"})
    secret = setup.json()["secret_b32"]
    enable = await client.post(
        "/api/account/2fa/enable",
        headers={"Authorization": f"Bearer {access}"},
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert enable.status_code == 200, enable.text
    return secret, enable.json()["recovery_codes"]


@pytest.mark.asyncio
async def test_routing_predicate_classifies_real_artifacts(make_user, client):
    """A real TOTP routes to /login; a real recovery code (XXXX-XXXX) never
    does — so one field can serve both with no toggle."""
    make_user(email=EMAIL, password=PW)
    secret, codes = await _enable_2fa(client, EMAIL, PW)
    assert _routes_to_totp(pyotp.TOTP(secret).now()) is True
    for rc in codes:
        assert re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}", rc), rc
        assert _routes_to_totp(rc) is False, f"recovery {rc!r} must not look like a TOTP"


@pytest.mark.asyncio
async def test_two_step_totp_path(make_user, client):
    """Step 1 (no code) → TOTP_REQUIRED → step 2 with a 6-digit code → 200."""
    make_user(email=EMAIL, password=PW)
    secret, _ = await _enable_2fa(client, EMAIL, PW)

    step1 = await _frontend_submit(client, EMAIL, PW, None)
    assert step1.status_code == 401
    assert step1.json()["code"] == "TOTP_REQUIRED"  # UI reveals the code step here

    step2 = await _frontend_submit(client, EMAIL, PW, pyotp.TOTP(secret).now())
    assert step2.status_code == 200, step2.text
    assert step2.json()["access_token"]


@pytest.mark.asyncio
async def test_two_step_recovery_path(make_user, client):
    """The same field accepts a recovery code → /login/recovery → 200, single-use."""
    make_user(email=EMAIL, password=PW)
    _secret, codes = await _enable_2fa(client, EMAIL, PW)

    assert (await _frontend_submit(client, EMAIL, PW, None)).json()["code"] == "TOTP_REQUIRED"

    ok = await _frontend_submit(client, EMAIL, PW, codes[0])
    assert ok.status_code == 200, ok.text
    assert ok.json()["access_token"]

    # Consumed: re-entering the same code routes to recovery again and fails.
    again = await _frontend_submit(client, EMAIL, PW, codes[0])
    assert again.status_code == 401
    assert again.json()["code"] == "INVALID_RECOVERY"


@pytest.mark.asyncio
async def test_no_2fa_is_single_step(make_user, client):
    """Without 2FA, step 1 logs in directly — the code step never appears."""
    make_user(email=EMAIL, password=PW)
    resp = await _frontend_submit(client, EMAIL, PW, None)
    assert resp.status_code == 200
    assert resp.json()["access_token"]


@pytest.mark.asyncio
async def test_wrong_code_is_reported_per_kind(make_user, client):
    """A 6-digit-but-wrong code → INVALID_TOTP; a malformed recovery code →
    INVALID_RECOVERY (the UI clears the field and lets you retry)."""
    make_user(email=EMAIL, password=PW)
    await _enable_2fa(client, EMAIL, PW)

    bad_totp = await _frontend_submit(client, EMAIL, PW, "000000")
    assert bad_totp.status_code == 401 and bad_totp.json()["code"] == "INVALID_TOTP"

    bad_rec = await _frontend_submit(client, EMAIL, PW, "ZZZZ-ZZZZ")
    assert bad_rec.status_code == 401 and bad_rec.json()["code"] == "INVALID_RECOVERY"
