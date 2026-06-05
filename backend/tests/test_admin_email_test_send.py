"""POST /api/admin/settings/email/test — synchronous SMTP smoke
that returns structured diagnostics."""
from __future__ import annotations

import pytest

from app.models.user import UserRole
from app.services import settings as settings_svc


def test_smtp_test_template_renders_in_site_timezone():
    """The test email's timestamp must go through the dt_locale filter (site
    timezone + label), not print a raw ISO string."""
    from datetime import datetime, timezone

    from app.services import email as email_svc

    dt = datetime(2026, 6, 5, 9, 26, 47, tzinfo=timezone.utc)
    out = email_svc._render(
        "en", "smtp_test", "txt", {"now": dt}, site_timezone="Europe/Vienna"
    )
    assert "(Europe/Vienna)" in out
    assert "2026-06-05T09:26:47" not in out  # no raw ISO
    assert "11:26" in out  # 09:26 UTC → 11:26 CEST


@pytest.mark.asyncio
async def test_test_send_returns_logs_fallback_when_unconfigured(
    make_user, db, client, login_as, monkeypatch
):
    monkeypatch.setattr("app.config.settings.SMTP_HOST", "")
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/admin/settings/email/test",
        json={"to": "ops@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error_class"] == "NotConfigured"


@pytest.mark.asyncio
async def test_test_send_success_path(
    make_user, db, client, login_as, monkeypatch
):
    """Mock aiosmtplib.send to a no-op; expect ok=True back."""
    monkeypatch.setattr("app.config.settings.SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr("app.config.settings.SMTP_PORT", 587)

    async def _fake_send(msg, **kwargs):
        return None

    import aiosmtplib

    monkeypatch.setattr(aiosmtplib, "send", _fake_send)
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/admin/settings/email/test",
        json={"to": "ops@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["error_class"] is None


@pytest.mark.asyncio
async def test_test_send_surfaces_smtp_exception_class_and_message(
    make_user, db, client, login_as, monkeypatch
):
    monkeypatch.setattr("app.config.settings.SMTP_HOST", "smtp.example.com")
    from aiosmtplib.errors import SMTPAuthenticationError

    async def _fake_send(msg, **kwargs):
        raise SMTPAuthenticationError(535, "wrong password")

    import aiosmtplib

    monkeypatch.setattr(aiosmtplib, "send", _fake_send)
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/admin/settings/email/test",
        json={"to": "ops@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error_class"] == "SMTPAuthenticationError"
    assert "wrong password" in body["error_message"]
    assert body["smtp_code"] == 535
    # The actionable hint is surfaced alongside the raw diagnostic.
    assert body["hint"] is not None
    assert "authentication" in body["hint"].lower()


@pytest.mark.asyncio
async def test_test_send_uses_override_without_persisting(
    make_user, db, client, login_as, monkeypatch
):
    """If `override` is supplied, those values are used for the test —
    persisted settings are not touched."""
    monkeypatch.setattr("app.config.settings.SMTP_HOST", "")  # would log-fallback
    captured: dict = {}

    async def _fake_send(msg, **kwargs):
        captured.update(kwargs)
        return None

    import aiosmtplib

    monkeypatch.setattr(aiosmtplib, "send", _fake_send)
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.post(
        "/api/admin/settings/email/test",
        json={
            "to": "ops@example.com",
            "override": {
                "host": "override.example",
                "port": 2525,
                "user": "ovuser",
                "password": "ovpass",
                "from_email": "ov@example",
                "from_name": "Override",
                "tls_mode": "none",
                "helo_hostname": "relay.override.example",
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert captured.get("hostname") == "override.example"
    assert captured.get("port") == 2525
    assert captured.get("username") == "ovuser"
    assert captured.get("password") == "ovpass"
    # The configurable EHLO/HELO name flows through to aiosmtplib.
    assert captured.get("local_hostname") == "relay.override.example"

    # Persisted settings remain empty.
    assert settings_svc.get(db, settings_svc.Keys.SMTP_HOST) is None


@pytest.mark.asyncio
async def test_test_send_admin_only(make_user, client, login_as):
    make_user(email="c@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("c@test.local", "Pass12345678!")
    resp = await client.post(
        "/api/admin/settings/email/test",
        json={"to": "x@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
