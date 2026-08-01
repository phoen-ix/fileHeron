"""Admin-editable site URL - kv override beats the APP_URL env."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.notification import Notification, NotificationCategory
from app.models.share import ShareKind
from app.models.user import UserRole
from app.services import settings as settings_svc
from app.services import site as site_svc

from ._share_helpers import land_file_and_announce


def _now_naive() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_get_returns_env_when_no_override(make_user, db, client, login_as):
    make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/settings/site",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_db_override"] is False
    assert body["env_app_url"]  # whatever the test env has set
    assert body["site_url"] == body["env_app_url"]


@pytest.mark.asyncio
async def test_put_writes_kv_and_get_returns_it(make_user, db, client, login_as):
    make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.put(
        "/api/admin/settings/site",
        json={"site_url": "https://files.example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["site_url"] == "https://files.example.com"
    assert body["has_db_override"] is True

    # Audit row exists with previous-effective + new-effective values.
    audits = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.site_url_changed.value)
        .all()
    )
    assert len(audits) == 1
    assert audits[0].extra["to"] == "https://files.example.com"


@pytest.mark.asyncio
async def test_put_null_clears_the_override(make_user, db, client, login_as):
    make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    headers = {"Authorization": f"Bearer {token}"}

    # Set, then clear.
    await client.put(
        "/api/admin/settings/site",
        json={"site_url": "https://files.example.com"},
        headers=headers,
    )
    resp = await client.put(
        "/api/admin/settings/site",
        json={"site_url": None},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_db_override"] is False
    assert body["site_url"] == body["env_app_url"]


@pytest.mark.asyncio
async def test_put_rejects_malformed_url(make_user, db, client, login_as):
    make_user(
        email="admin@test.local", role=UserRole.admin, password="Pass12345678!"
    )
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    for bad in ["not-a-url", "ftp://files.example.com", "https://"]:
        resp = await client.put(
            "/api/admin/settings/site",
            json={"site_url": bad},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, f"{bad!r} should be rejected, got {resp.status_code}"


def test_share_notification_uses_kv_override_url(make_user, db):
    """The share-created notification's link_url should reflect the
    kv override at dispatch time. Trips the wiring all the way from
    settings → services.site.get_site_url → services.share.create_share's
    notification dispatch."""
    sender = make_user(email="s@test.local", role=UserRole.admin)
    recipient = make_user(email="r@test.local", role=UserRole.client)

    settings_svc.set_value(
        db,
        key=settings_svc.Keys.SITE_URL,
        value="https://files.example.com",
        actor=None,
    )
    db.commit()

    # Direct service call so the test stays fast (skip the full HTTP +
    # auth path covered by the route tests above).
    from app.services.share import create_share as _create
    share = _create(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        expires_at=_now_naive() + timedelta(hours=1),
        recipient_user_ids=[recipient.id],
        recipient_group_ids=[],
        subject="t",
    )
    # The announcement is deferred until the share's uploads land (audit #2).
    land_file_and_announce(db, share, sender)
    db.commit()

    notif = (
        db.query(Notification)
        .filter(
            Notification.user_id == recipient.id,
            Notification.category == NotificationCategory.share_created,
        )
        .one()
    )
    assert notif.link_url == f"https://files.example.com/share/{share.id}"


def test_get_site_url_helper_strips_trailing_slash(make_user, db):
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.SITE_URL,
        value="https://files.example.com/",
        actor=None,
    )
    db.commit()
    assert site_svc.get_site_url(db) == "https://files.example.com"
