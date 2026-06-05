"""The password endpoints (reset-password, change-password) are gated by the
same per-IP rate limit as login/forgot/verify. The limiter itself is Redis-
backed (fail-open without Redis), so we assert the gate is *wired* by forcing
`check_ip_allowed` to deny and expecting a 429."""
from __future__ import annotations

import pytest

from app.models.user import UserRole
from app.services import rate_limit as rate_limit_svc


@pytest.mark.asyncio
async def test_reset_password_rate_limited(client, monkeypatch):
    monkeypatch.setattr(rate_limit_svc, "check_ip_allowed", lambda *a, **k: False)
    resp = await client.post(
        "/api/auth/reset-password",
        json={"token": "abcdefghij0123", "new_password": "LongCorrectHorse123!"},
    )
    assert resp.status_code == 429
    assert resp.json()["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_change_password_rate_limited(make_user, client, login_as, monkeypatch):
    make_user(email="alice@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("alice@test.local", "Pass12345678!")
    monkeypatch.setattr(rate_limit_svc, "check_ip_allowed", lambda *a, **k: False)
    resp = await client.post(
        "/api/account/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "Pass12345678!", "new_password": "AnotherLong123!"},
    )
    assert resp.status_code == 429
    assert resp.json()["code"] == "RATE_LIMITED"
