"""Client ↔ employee connection auto-population."""
from __future__ import annotations

import pytest

from app.models.client_employee_connection import (
    ClientEmployeeConnection,
    ConnectionSource,
)
from app.models.user import UserRole
from app.services import connection as connection_svc
from app.services import group as group_svc


@pytest.mark.asyncio
async def test_invite_records_sticky_connection(make_user, db):
    employee = make_user(email="emp@test.local", role=UserRole.employee)
    client_user = make_user(email="cli@test.local", role=UserRole.client)
    connection_svc.record_invite_connection(
        db, inviter=employee, invitee=client_user
    )
    db.commit()
    rows = (
        db.query(ClientEmployeeConnection)
        .filter(
            ClientEmployeeConnection.client_user_id == client_user.id,
            ClientEmployeeConnection.employee_user_id == employee.id,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].source == ConnectionSource.invite


@pytest.mark.asyncio
async def test_invite_idempotent(make_user, db):
    employee = make_user(email="emp@test.local", role=UserRole.employee)
    client_user = make_user(email="cli@test.local", role=UserRole.client)
    connection_svc.record_invite_connection(db, inviter=employee, invitee=client_user)
    connection_svc.record_invite_connection(db, inviter=employee, invitee=client_user)
    db.commit()
    assert (
        db.query(ClientEmployeeConnection)
        .filter(ClientEmployeeConnection.source == ConnectionSource.invite)
        .count()
        == 1
    )


@pytest.mark.asyncio
async def test_employee_to_employee_no_connection(make_user, db):
    e1 = make_user(email="e1@test.local", role=UserRole.employee)
    e2 = make_user(email="e2@test.local", role=UserRole.employee)
    connection_svc.record_invite_connection(db, inviter=e1, invitee=e2)
    db.commit()
    assert db.query(ClientEmployeeConnection).count() == 0


@pytest.mark.asyncio
async def test_invite_source_persists_after_shared_group_loss(make_user, db):
    """An invite-sourced connection must NOT disappear when shared groups go
    away. (The two sources are independent rows.)"""
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    employee = make_user(email="emp@test.local", role=UserRole.employee)
    client_user = make_user(email="cli@test.local", role=UserRole.client)

    # Seed both: invite + shared_group via a temporary group.
    connection_svc.record_invite_connection(db, inviter=employee, invitee=client_user)
    g = group_svc.create_group(
        db, actor=admin, name="tmp", description=None, is_company_inbox=False
    )
    db.commit()
    group_svc.add_member(db, actor=admin, group=g, user=employee)
    group_svc.add_member(db, actor=admin, group=g, user=client_user)
    db.commit()

    # Both sources present.
    sources = {
        r.source
        for r in db.query(ClientEmployeeConnection).filter(
            ClientEmployeeConnection.client_user_id == client_user.id,
            ClientEmployeeConnection.employee_user_id == employee.id,
        )
    }
    assert sources == {ConnectionSource.invite, ConnectionSource.shared_group}

    # Remove one party - shared_group goes; invite stays.
    group_svc.remove_member(db, actor=admin, group=g, user=client_user)
    db.commit()
    sources = {
        r.source
        for r in db.query(ClientEmployeeConnection).filter(
            ClientEmployeeConnection.client_user_id == client_user.id,
            ClientEmployeeConnection.employee_user_id == employee.id,
        )
    }
    assert sources == {ConnectionSource.invite}


@pytest.mark.asyncio
async def test_list_employees_visible_to_client(make_user, db):
    e1 = make_user(email="e1@test.local", role=UserRole.employee)
    e2 = make_user(email="e2@test.local", role=UserRole.employee)
    e_other = make_user(email="other@test.local", role=UserRole.employee)
    client_user = make_user(email="cli@test.local", role=UserRole.client)
    connection_svc.record_invite_connection(db, inviter=e1, invitee=client_user)
    connection_svc.record_invite_connection(db, inviter=e2, invitee=client_user)
    db.commit()
    visible = connection_svc.list_employees_visible_to(db, viewer=client_user)
    assert {u.id for u in visible} == {e1.id, e2.id}
    assert e_other.id not in {u.id for u in visible}
