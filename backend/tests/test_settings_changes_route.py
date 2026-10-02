"""GET /api/admin/audit-log/settings-changes - the Overview's panel feed."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole
from app.utils.timeutil import utc_now

PW = "Pass12345678!"


async def _admin(make_user, login_as):
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    token, _ = await login_as("admin@test.local", PW)
    return admin, {"Authorization": f"Bearer {token}"}


def _row(db, event, *, target_type="settings", target_id=None, at=None, actor=None, extra=None):
    row = AuditLog(
        event_type=event.value, target_type=target_type, target_id=target_id,
        actor_user_id=actor, extra=extra, created_at=at or utc_now(),
    )
    db.add(row)
    db.flush()
    return row


@pytest.mark.asyncio
async def test_lists_settings_changes_newest_first_with_the_actor(db, make_user, login_as, client):
    admin, h = await _admin(make_user, login_as)
    now = utc_now().replace(microsecond=0)
    _row(db, AuditEventType.smtp_config_changed, target_id="smtp", at=now - timedelta(minutes=5), actor=admin.id)
    first = _row(db, AuditEventType.legal_changed, target_id="legal", at=now, actor=admin.id)
    second = _row(db, AuditEventType.twofa_policy_changed, target_id="twofa_policy", at=now)
    _row(db, AuditEventType.cron_schedule_changed, target_type="cron", target_id="imap_poll",
         at=now - timedelta(minutes=1))
    _row(db, AuditEventType.smtp_test_foreign_target, target_id="smtp", at=now)
    _row(db, AuditEventType.login_success, target_type="user", target_id="1", at=now)
    db.commit()

    r = await client.get("/api/admin/audit-log/settings-changes", headers=h)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["event_type"] for i in items] == [
        "twofa_policy_changed", "legal_changed", "cron_schedule_changed", "smtp_config_changed",
    ]
    assert items[0]["id"] == second.id and items[1]["id"] == first.id  # same second: id decides
    assert items[1]["actor_display_name"] is not None


@pytest.mark.asyncio
async def test_the_limit_is_honoured_and_bounded(db, make_user, login_as, client):
    _admin_user, h = await _admin(make_user, login_as)
    for i in range(12):
        _row(db, AuditEventType.motd_changed, target_id="motd", at=utc_now() - timedelta(seconds=i))
    db.commit()
    assert len((await client.get("/api/admin/audit-log/settings-changes", headers=h)).json()["items"]) == 8
    r = await client.get("/api/admin/audit-log/settings-changes?limit=3", headers=h)
    assert len(r.json()["items"]) == 3
    assert (await client.get("/api/admin/audit-log/settings-changes?limit=500", headers=h)).status_code == 422


@pytest.mark.asyncio
async def test_admins_only(make_user, login_as, client):
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    token, _ = await login_as("emp@test.local", PW)
    r = await client.get(
        "/api/admin/audit-log/settings-changes", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403
