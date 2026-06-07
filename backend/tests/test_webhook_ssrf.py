"""M2/M9: webhook URLs are SSRF-guarded at create AND at delivery time."""
from __future__ import annotations

import pytest

from app.models.user import UserRole


@pytest.fixture
def admin_headers(make_user, login_as):
    async def _go():
        make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
        token, _ = await login_as("admin@test.local", "Pass12345678!")
        return {"Authorization": f"Bearer {token}"}

    return _go


@pytest.mark.asyncio
async def test_create_blocks_ssrf_url(client, admin_headers):
    headers = await admin_headers()
    for bad in ("http://127.0.0.1:6379/", "https://169.254.169.254/latest/meta-data/"):
        r = await client.post(
            "/api/admin/webhooks",
            json={"name": "x", "url": bad, "event_types": ["share_created"]},
            headers=headers,
        )
        assert r.status_code == 400, r.text
        assert r.json()["code"] in ("URL_BLOCKED", "URL_NOT_ALLOWED")


@pytest.mark.asyncio
async def test_delivery_blocks_ssrf_url(db, make_user, monkeypatch):
    """A webhook row pointing at an internal target (e.g. one slipped in via a
    config-backup import) is blocked at delivery and never POSTed."""
    from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus
    from app.utils.crypto import encrypt_setting
    from app.workers import webhook_deliver as wd_mod
    from app.workers.webhook_deliver import webhook_deliver

    admin = make_user(email="a@test.local", role=UserRole.admin)
    wh = Webhook(
        name="evil",
        url="http://127.0.0.1:6379/",
        secret_encrypted=encrypt_setting("s"),
        event_types=["*"],
        active=True,
        created_by_id=admin.id,
    )
    db.add(wh)
    db.commit()

    monkeypatch.setattr(wd_mod, "SessionLocal", lambda: db)
    result = await webhook_deliver(None, wh.id, "webhook.ping", {"metadata": {}})

    # Blocked terminally (not "retry", which is what a real refused POST would give).
    assert result["status"] == "blocked"
    db.expire_all()
    d = db.query(WebhookDelivery).filter(WebhookDelivery.webhook_id == wh.id).one()
    assert d.status == WebhookDeliveryStatus.failed
    assert "blocked" in (d.error or "")
