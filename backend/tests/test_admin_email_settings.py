"""Admin email/SMTP settings endpoints - GET masking, PUT semantics,
audit row (post-Phase 10)."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole
from app.services import settings as settings_svc


@pytest.mark.asyncio
async def test_get_email_settings_masks_password(
    make_user, db, client, login_as
):
    admin = make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.SMTP_PASSWORD,
        value="ssh-do-not-leak",
        actor=admin,
    )
    db.commit()

    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/settings/email",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Secret never appears in the response.
    assert "password" not in body
    assert body["is_password_set"] is True
    assert "ssh-do-not-leak" not in resp.text


@pytest.mark.asyncio
async def test_put_email_settings_persists_and_audits(
    make_user, db, client, login_as
):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.put(
        "/api/admin/settings/email",
        json={
            "host": "smtp.example.com",
            "port": 2525,
            "user": "fh-bot",
            "password": "freshly-set-secret",
            "from_email": "noreply@example.com",
            "from_name": "fileHeron",
            "tls_mode": "starttls",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    db.expire_all()
    assert (
        settings_svc.get(db, settings_svc.Keys.SMTP_HOST)
        == "smtp.example.com"
    )
    # Audit event written.
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.smtp_config_changed.value)
        .all()
    )
    assert len(rows) == 1
    assert "smtp.password" in rows[0].extra["keys"]
    assert "smtp.host" in rows[0].extra["keys"]


@pytest.mark.asyncio
async def test_put_with_null_password_keeps_existing(
    make_user, db, client, login_as
):
    admin = make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.SMTP_PASSWORD,
        value="original-secret",
        actor=admin,
    )
    db.commit()
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.put(
        "/api/admin/settings/email",
        json={
            "host": "new.example.com",
            # password omitted → keep existing
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert (
        settings_svc.get(db, settings_svc.Keys.SMTP_PASSWORD)
        == "original-secret"
    )


@pytest.mark.asyncio
async def test_put_with_empty_password_clears(
    make_user, db, client, login_as
):
    admin = make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.SMTP_PASSWORD,
        value="will-be-cleared",
        actor=admin,
    )
    db.commit()
    token, _ = await login_as("admin@test.local", "Pass12345678!")

    resp = await client.put(
        "/api/admin/settings/email",
        json={"password": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert settings_svc.get(db, settings_svc.Keys.SMTP_PASSWORD) is None


@pytest.mark.asyncio
async def test_put_admin_only(make_user, client, login_as):
    make_user(email="u@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("u@test.local", "Pass12345678!")
    resp = await client.put(
        "/api/admin/settings/email",
        json={"host": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
