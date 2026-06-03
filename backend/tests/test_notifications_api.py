"""/api/notifications/* endpoints — list / unread / mark / read-all."""
from __future__ import annotations

import pytest

from app.models.notification import Notification, NotificationCategory
from app.models.user import UserRole


def _seed(db, user_id, n=3, mark_read=0):
    for i in range(n):
        db.add(
            Notification(
                user_id=user_id,
                category=NotificationCategory.share_created,
                payload_json={"i": i},
                link_url=f"https://example.com/x/{i}",
            )
        )
    db.commit()
    if mark_read:
        from datetime import datetime, timezone

        rows = db.query(Notification).filter(Notification.user_id == user_id).all()
        for r in rows[:mark_read]:
            r.read_at = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        db.commit()


@pytest.mark.asyncio
async def test_list_returns_paginated_with_unread_count(make_user, db, client, login_as):
    user = make_user(email="u@test.local", role=UserRole.client, password="Pass12345678!")
    _seed(db, user.id, n=5, mark_read=2)
    token, _ = await login_as("u@test.local", "Pass12345678!")
    # Phase 7 fires a login_alert notification on every login (the test
    # always presents a "new" device). Account for it in the totals.
    resp = await client.get(
        "/api/notifications", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 6  # 5 seeded + 1 login_alert
    assert body["unread_count"] == 4  # 3 unread seeded + 1 login_alert
    assert len(body["items"]) == 6


@pytest.mark.asyncio
async def test_list_unread_filter(make_user, db, client, login_as):
    user = make_user(email="u@test.local", role=UserRole.client, password="Pass12345678!")
    _seed(db, user.id, n=4, mark_read=1)
    token, _ = await login_as("u@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/notifications?unread=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 4  # 3 unread seeded + 1 login_alert
    assert all(item["read_at"] is None for item in body["items"])


@pytest.mark.asyncio
async def test_delete_one_hard_deletes_row(make_user, db, client, login_as):
    user = make_user(email="u@test.local", role=UserRole.client, password="Pass12345678!")
    _seed(db, user.id, n=2)
    token, _ = await login_as("u@test.local", "Pass12345678!")
    n_id = db.query(Notification).filter(Notification.user_id == user.id).first().id
    resp = await client.delete(
        f"/api/notifications/{n_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    # The row is GONE from the DB (hard delete, no soft-delete residue).
    db.expire_all()
    assert (
        db.query(Notification).filter(Notification.id == n_id).count() == 0
    )


@pytest.mark.asyncio
async def test_delete_all_hard_deletes_callers_rows_only(make_user, db, client, login_as):
    user = make_user(email="u@test.local", role=UserRole.client, password="Pass12345678!")
    other = make_user(email="other@test.local", role=UserRole.client)
    _seed(db, user.id, n=4)
    _seed(db, other.id, n=2)
    token, _ = await login_as("u@test.local", "Pass12345678!")
    resp = await client.delete(
        "/api/notifications",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["unread_count"] == 0
    db.expire_all()
    assert db.query(Notification).filter(Notification.user_id == user.id).count() == 0
    # Another user's notifications are untouched.
    assert db.query(Notification).filter(Notification.user_id == other.id).count() == 2


@pytest.mark.asyncio
async def test_delete_other_users_notification_returns_404(make_user, db, client, login_as):
    make_user(email="me@test.local", role=UserRole.client, password="Pass12345678!")
    other = make_user(email="other@test.local", role=UserRole.client)
    _seed(db, other.id, n=1)
    n_id = db.query(Notification).filter(Notification.user_id == other.id).first().id
    token, _ = await login_as("me@test.local", "Pass12345678!")
    resp = await client.delete(
        f"/api/notifications/{n_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    # The other user's row survives the cross-user attempt.
    db.expire_all()
    assert db.query(Notification).filter(Notification.id == n_id).count() == 1


@pytest.mark.asyncio
async def test_preferences_hide_admin_only_categories_from_non_admin(
    make_user, client, login_as
):
    make_user(email="u@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("u@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/notifications/preferences",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    cats = {it["category"] for it in resp.json()["items"]}
    assert "release_available" not in cats
    assert "ops_alert" not in cats
    assert "share_created" in cats  # normal categories still present


@pytest.mark.asyncio
async def test_preferences_include_admin_only_categories_for_admin(
    make_user, client, login_as
):
    make_user(email="a@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("a@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/notifications/preferences",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    cats = {it["category"] for it in resp.json()["items"]}
    assert {"release_available", "ops_alert"} <= cats


@pytest.mark.asyncio
async def test_non_admin_cannot_set_admin_only_preference(
    make_user, client, login_as
):
    make_user(email="u@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("u@test.local", "Pass12345678!")
    resp = await client.put(
        "/api/notifications/preferences",
        headers={"Authorization": f"Bearer {token}"},
        json={"preferences": {"release_available": "off"}},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "INVALID_CATEGORY"


@pytest.mark.asyncio
async def test_admin_can_set_admin_only_preference(make_user, client, login_as):
    make_user(email="a@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("a@test.local", "Pass12345678!")
    resp = await client.put(
        "/api/notifications/preferences",
        headers={"Authorization": f"Bearer {token}"},
        json={"preferences": {"release_available": "in_app"}},
    )
    assert resp.status_code == 200, resp.text
    chan = {
        it["category"]: it["channel"] for it in resp.json()["items"]
    }["release_available"]
    assert chan == "in_app"


@pytest.mark.asyncio
async def test_oidc_linked_hidden_when_no_sso_provider(make_user, client, login_as):
    make_user(email="u@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("u@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/notifications/preferences",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    cats = {it["category"] for it in resp.json()["items"]}
    assert "oidc_linked" not in cats
    put = await client.put(
        "/api/notifications/preferences",
        headers={"Authorization": f"Bearer {token}"},
        json={"preferences": {"oidc_linked": "off"}},
    )
    assert put.status_code == 400, put.text
    assert put.json()["code"] == "INVALID_CATEGORY"


@pytest.mark.asyncio
async def test_oidc_linked_shown_when_sso_enabled(
    make_user, make_provider, client, login_as
):
    make_user(email="u@test.local", role=UserRole.client, password="Pass12345678!")
    make_provider()  # enabled OIDC provider (conftest default)
    token, _ = await login_as("u@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/notifications/preferences",
        headers={"Authorization": f"Bearer {token}"},
    )
    cats = {it["category"] for it in resp.json()["items"]}
    assert "oidc_linked" in cats
    put = await client.put(
        "/api/notifications/preferences",
        headers={"Authorization": f"Bearer {token}"},
        json={"preferences": {"oidc_linked": "in_app"}},
    )
    assert put.status_code == 200, put.text
