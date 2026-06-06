"""Anonymous token-authed subscription management
(routers/notification_subscriptions.py)."""
from __future__ import annotations

import pytest

from app.models.notification import NotificationCategory
from app.models.user import UserRole
from app.models.user_notification_preference import (
    NotificationChannel,
    UserNotificationPreference,
)
from app.services import unsubscribe_token as tok


@pytest.mark.asyncio
async def test_get_returns_items_and_name(make_user, client):
    user = make_user(email="u@test.local", role=UserRole.client, display_name="Dana")
    t = tok.issue(user.id)
    resp = await client.get(f"/api/notification-subscriptions/{t}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["display_name"] == "Dana"
    cats = {i["category"] for i in body["items"]}
    assert "share_created" in cats
    # login_alert is shown but locked.
    la = next(i for i in body["items"] if i["category"] == "login_alert")
    assert la["locked"] is True


@pytest.mark.asyncio
async def test_bad_token_401(client):
    resp = await client.get("/api/notification-subscriptions/garbage")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_expired_token_401(make_user, client):
    user = make_user(email="u@test.local")
    t = tok.issue(user.id, ttl_sec=-5)
    resp = await client.get(f"/api/notification-subscriptions/{t}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_put_updates_preference(make_user, db, client):
    user = make_user(email="u@test.local", role=UserRole.client)
    t = tok.issue(user.id)
    resp = await client.put(
        f"/api/notification-subscriptions/{t}",
        json={"preferences": {"share_created": "off"}},
    )
    assert resp.status_code == 200, resp.text
    row = (
        db.query(UserNotificationPreference)
        .filter(
            UserNotificationPreference.user_id == user.id,
            UserNotificationPreference.category == NotificationCategory.share_created,
        )
        .one()
    )
    assert row.channel == NotificationChannel.off


@pytest.mark.asyncio
async def test_put_rejects_locked_category(make_user, client):
    user = make_user(email="u@test.local")
    t = tok.issue(user.id)
    resp = await client.put(
        f"/api/notification-subscriptions/{t}",
        json={"preferences": {"login_alert": "off"}},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "LOCKED_CATEGORY"


@pytest.mark.asyncio
async def test_unsubscribe_sets_off_and_returns_prior(make_user, db, client):
    user = make_user(email="u@test.local")
    t = tok.issue(user.id)
    resp = await client.post(
        f"/api/notification-subscriptions/{t}/unsubscribe",
        json={"category": "share_created"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["previous_channel"] == "both"  # default for share_created
    sc = next(i for i in body["items"] if i["category"] == "share_created")
    assert sc["channel"] == "off"


@pytest.mark.asyncio
async def test_unsubscribe_refuses_locked(make_user, client):
    user = make_user(email="u@test.local")
    t = tok.issue(user.id)
    resp = await client.post(
        f"/api/notification-subscriptions/{t}/unsubscribe",
        json={"category": "login_alert"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "LOCKED_CATEGORY"


@pytest.mark.asyncio
async def test_one_click_opts_out(make_user, db, client):
    user = make_user(email="u@test.local")
    t = tok.issue(user.id)
    resp = await client.post(
        f"/api/notification-subscriptions/{t}/one-click?category=share_created",
        content="List-Unsubscribe=One-Click",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    row = (
        db.query(UserNotificationPreference)
        .filter(
            UserNotificationPreference.user_id == user.id,
            UserNotificationPreference.category == NotificationCategory.share_created,
        )
        .one()
    )
    assert row.channel == NotificationChannel.off


@pytest.mark.asyncio
async def test_one_click_locked_is_noop_200(make_user, db, client):
    """A locked category never gets a one-click URL, but if posted anyway the
    endpoint must not 4xx the mail client - it no-ops with 200."""
    user = make_user(email="u@test.local")
    t = tok.issue(user.id)
    resp = await client.post(
        f"/api/notification-subscriptions/{t}/one-click?category=login_alert",
        content="List-Unsubscribe=One-Click",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    # login_alert is locked -> no row written.
    row = (
        db.query(UserNotificationPreference)
        .filter(
            UserNotificationPreference.user_id == user.id,
            UserNotificationPreference.category == NotificationCategory.login_alert,
        )
        .one_or_none()
    )
    assert row is None


@pytest.mark.asyncio
async def test_admin_only_categories_hidden_for_clients(make_user, client):
    user = make_user(email="u@test.local", role=UserRole.client)
    t = tok.issue(user.id)
    resp = await client.get(f"/api/notification-subscriptions/{t}")
    cats = {i["category"] for i in resp.json()["items"]}
    assert "ops_alert" not in cats
    assert "inbound_message" not in cats


@pytest.mark.asyncio
async def test_disabled_user_token_401(make_user, client):
    user = make_user(email="u@test.local", is_disabled=True)
    t = tok.issue(user.id)
    resp = await client.get(f"/api/notification-subscriptions/{t}")
    assert resp.status_code == 401
