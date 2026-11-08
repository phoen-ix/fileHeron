"""Change-password and change-email spend separate per-IP buckets.

They shared `change_password`, so a mistyped password on the email form spent
the password form's 3-per-15-minutes budget and the other way round
(audit 2026-11-08)."""
from __future__ import annotations

import pytest

from app.models.user import UserRole

PW = "Pass12345678!"


@pytest.mark.asyncio
async def test_each_form_has_its_own_bucket(make_user, login_as, client, db, monkeypatch):
    from app.services import rate_limit
    from app.services import settings as settings_svc

    settings_svc.set_value(db, key=settings_svc.Keys.EMAIL_CHANGE_SELF_SERVICE, value="true", actor=None)
    db.commit()

    seen: list[str] = []
    monkeypatch.setattr(rate_limit, "check_ip_allowed", lambda bucket, *_a, **_k: seen.append(bucket) or True)
    make_user(email="u@test.local", role=UserRole.employee, password=PW)
    token, _ = await login_as("u@test.local", PW)
    h = {"Authorization": f"Bearer {token}"}

    wrong = "wrong-one!!"
    await client.post(
        "/api/account/change-password",
        json={"current_password": wrong, "new_password": "AnotherPass123!"},
        headers=h,
    )
    await client.post("/api/account/email", json={"new_email": "n@test.local", "current_password": wrong}, headers=h)

    assert "change_password" in seen and "change_email" in seen, seen
