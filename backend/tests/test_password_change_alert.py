"""L34: a self-service password change must send a security alert to the
account owner, and the new ``password_changed`` template must render in both
locales."""
from __future__ import annotations

import pytest

from app.models.user import UserRole
from app.services import email as email_svc


@pytest.mark.asyncio
async def test_change_password_sends_owner_alert(make_user, client, login_as, monkeypatch):
    make_user(email="cp@test.local", password="OldPassword123!", role=UserRole.employee)
    token, _cookies = await login_as("cp@test.local", "OldPassword123!")

    calls: list[dict] = []

    async def _spy(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(email_svc, "send_password_changed_email", _spy)

    resp = await client.post(
        "/api/account/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "OldPassword123!", "new_password": "BrandNewPass456!"},
    )
    assert resp.status_code == 200, resp.text
    assert len(calls) == 1
    assert calls[0]["to"] == "cp@test.local"


@pytest.mark.parametrize("locale", ["en", "de"])
def test_password_changed_template_renders(locale):
    subject, text, _html = email_svc.render_email(
        locale,
        "password_changed",
        {"display_name": "Sam", "ip_hint": "~u4pr", "reset_url": "http://x/forgot-password"},
        app_url="http://x",
        app_name="fileHeron",
    )
    assert subject  # subjects.json entry exists for both locales
    assert "Sam" in text
    assert "http://x/forgot-password" in text
