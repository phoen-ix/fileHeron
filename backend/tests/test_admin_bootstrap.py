"""Admin bootstrap is idempotent and respects env."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import User, UserRole
from app.services.admin_bootstrap import bootstrap_admin_if_configured


@pytest.mark.asyncio
async def test_bootstrap_skips_when_email_empty(db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_EMAIL", "")
    bootstrap_admin_if_configured(db)
    assert db.query(User).count() == 0


@pytest.mark.asyncio
async def test_bootstrap_creates_admin_when_password_set_and_no_admin(db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_EMAIL", "boot@test.local")
    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_PASSWORD", "BootstrapPass123!")
    bootstrap_admin_if_configured(db)

    db.expire_all()
    admins = db.query(User).filter(User.role == UserRole.admin).all()
    assert len(admins) == 1
    assert admins[0].email_verified is True


@pytest.mark.asyncio
async def test_bootstrap_promotes_existing_user(make_user, db, monkeypatch):
    from app.config import settings

    user = make_user(email="boot@test.local", password="ExistingPass123!", role=UserRole.client, email_verified=False)
    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_EMAIL", "boot@test.local")
    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_PASSWORD", "")  # not used in this path

    bootstrap_admin_if_configured(db)

    db.expire_all()
    refreshed = db.query(User).filter(User.id == user.id).one()
    assert refreshed.role == UserRole.admin
    assert refreshed.email_verified is True


@pytest.mark.asyncio
async def test_bootstrap_idempotent_on_second_run(make_user, db, monkeypatch):
    from app.config import settings

    make_user(email="boot@test.local", password="x", role=UserRole.admin, email_verified=True)
    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_EMAIL", "boot@test.local")

    before = db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.admin_bootstrapped.value
    ).count()
    bootstrap_admin_if_configured(db)
    after = db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.admin_bootstrapped.value
    ).count()
    # No change-of-state → no audit row added.
    assert after == before


@pytest.mark.asyncio
async def test_bootstrap_does_not_create_second_admin(make_user, db, monkeypatch):
    from app.config import settings

    make_user(email="existing-admin@test.local", role=UserRole.admin)
    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_EMAIL", "different@test.local")
    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_PASSWORD", "WouldBeNewPass123!")

    bootstrap_admin_if_configured(db)

    db.expire_all()
    admins = db.query(User).filter(User.role == UserRole.admin).all()
    assert len(admins) == 1, "should not create a second admin when one already exists"


@pytest.mark.asyncio
async def test_bootstrap_promote_existing_client_prunes_connections(
    make_user, db, monkeypatch
):
    """Promoting a client → admin via bootstrap cleans the client-slot
    connection rows; otherwise they'd describe a client↔admin pairing
    where the 'client' is actually an admin now."""
    from app.config import settings
    from app.models.client_employee_connection import ClientEmployeeConnection
    from app.services import connection as connection_svc

    employer = make_user(email="emp@test.local", role=UserRole.employee)
    target = make_user(
        email="boot@test.local",
        password="ExistingPass123!",
        role=UserRole.client,
        email_verified=False,
    )
    connection_svc.record_invite_connection(db, inviter=employer, invitee=target)
    db.commit()
    assert (
        db.query(ClientEmployeeConnection)
        .filter(ClientEmployeeConnection.client_user_id == target.id)
        .count()
        == 1
    )

    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_EMAIL", "boot@test.local")
    monkeypatch.setattr(settings, "ADMIN_BOOTSTRAP_PASSWORD", "")
    bootstrap_admin_if_configured(db)

    db.expire_all()
    assert (
        db.query(ClientEmployeeConnection)
        .filter(ClientEmployeeConnection.client_user_id == target.id)
        .count()
        == 0
    )
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.admin_bootstrapped.value)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit.extra.get("connections_pruned") == 1
