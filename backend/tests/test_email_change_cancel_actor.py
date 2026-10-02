"""Who cancelled a pending email change is who the audit log names.

The admin cancel route called the service's `user=` branch, which recorded the
TARGET as the actor - so the log said the user withdrew a change an admin had
withdrawn for them. The self-cancel keeps naming the user."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole
from app.services import email_change as email_change_svc

PW = "Pass12345678!"


def _pending(db, user, admin):
    outcome = email_change_svc.request_email_change(
        db, target=user, new_email="new@test.local", initiated_by=admin,
        request=None, skip_verification=False,
    )
    db.commit()
    assert outcome.applied is False


def _cancel_rows(db):
    return (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.email_change_cancelled.value)
        .all()
    )


@pytest.mark.asyncio
async def test_an_admin_cancel_is_audited_as_the_admin(db, make_user, login_as, client):
    user = make_user(email="me@test.local", role=UserRole.employee)
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    _pending(db, user, admin)
    token, _ = await login_as("admin@test.local", PW)

    r = await client.delete(
        f"/api/admin/users/{user.id}/email", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "cancelled": 1}
    db.expire_all()
    (row,) = _cancel_rows(db)
    assert row.actor_user_id == admin.id
    assert row.target_id == str(user.id)
    assert row.extra["via"] == "admin_revoke"


def test_a_self_cancel_still_names_the_user(db, make_user):
    user = make_user(email="me@test.local", role=UserRole.employee)
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    _pending(db, user, admin)
    assert email_change_svc.cancel_email_change(db, user=user, actor=user) == 1
    db.commit()
    (row,) = _cancel_rows(db)
    assert row.actor_user_id == user.id
    assert row.extra["via"] == "user_revoke"
