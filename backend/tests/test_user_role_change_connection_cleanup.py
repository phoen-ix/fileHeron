"""Role changes via `update_user` clean up `ClientEmployeeConnection` rows
where the target sat in the now-wrong slot (the column naming on the
connection table is slot-specific). Slot-preserving transitions
(employee↔admin) leave the rows alone.

This pairs with the validator-side guard in `_validate_inbound_targets`:
the validator stops the security risk regardless, but data hygiene at
the cause site keeps `/api/users/search`, the recipient picker, and
`_connected_*_ids_of` from returning ghost connections.
"""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.client_employee_connection import (
    ClientEmployeeConnection,
)
from app.models.user import UserRole
from app.services import connection as connection_svc
from app.services import group as group_svc
from app.services import user_management as um_svc


def test_demote_employee_drops_their_employee_slot_connections(
    make_user, db
):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    alice = make_user(email="alice@test.local", role=UserRole.employee)
    bob = make_user(email="bob@test.local", role=UserRole.client)
    connection_svc.record_invite_connection(db, inviter=alice, invitee=bob)
    db.commit()

    # Sanity precondition.
    assert (
        db.query(ClientEmployeeConnection)
        .filter(
            ClientEmployeeConnection.employee_user_id == alice.id,
            ClientEmployeeConnection.client_user_id == bob.id,
        )
        .count()
        == 1
    )

    um_svc.update_user(db, actor=admin, target=alice, role=UserRole.client)
    db.commit()

    assert (
        db.query(ClientEmployeeConnection)
        .filter(ClientEmployeeConnection.employee_user_id == alice.id)
        .count()
        == 0
    )
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.role_changed.value)
        .one()
    )
    assert audit.extra["changes"]["role"] == {
        "from": "employee",
        "to": "client",
    }
    assert audit.extra["changes"]["connections_pruned"] == 1


def test_promote_client_drops_their_client_slot_connections(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    employer = make_user(email="alice@test.local", role=UserRole.employee)
    bob = make_user(email="bob@test.local", role=UserRole.client)
    connection_svc.record_invite_connection(db, inviter=employer, invitee=bob)
    db.commit()

    um_svc.update_user(db, actor=admin, target=bob, role=UserRole.employee)
    db.commit()

    assert (
        db.query(ClientEmployeeConnection)
        .filter(ClientEmployeeConnection.client_user_id == bob.id)
        .count()
        == 0
    )


def test_employee_to_admin_preserves_connections(make_user, db):
    """Slot-preserving transition: both employee and admin sit on the
    employee_user_id side. No cleanup should run."""
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    alice = make_user(email="alice@test.local", role=UserRole.employee)
    bob = make_user(email="bob@test.local", role=UserRole.client)
    connection_svc.record_invite_connection(db, inviter=alice, invitee=bob)
    db.commit()

    um_svc.update_user(db, actor=admin, target=alice, role=UserRole.admin)
    db.commit()

    assert (
        db.query(ClientEmployeeConnection)
        .filter(
            ClientEmployeeConnection.employee_user_id == alice.id,
            ClientEmployeeConnection.client_user_id == bob.id,
        )
        .count()
        == 1
    )
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.role_changed.value)
        .one()
    )
    assert "connections_pruned" not in audit.extra["changes"]


def test_demote_employee_repopulates_new_slot_shared_group_rows(
    make_user, db
):
    """Alice (employee) and Bob (employee) and Charlie (client) all share
    a group. Pre-demotion shared_group rows: Charlie↔Alice, Charlie↔Bob.
    After Alice is demoted to client, we expect:
      - Charlie↔Alice gone (Alice was on employee slot)
      - Charlie↔Bob untouched (no cleanup involves Charlie or Bob)
      - Alice↔Bob added (Alice now client, Bob employee, they share G)
    """
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    alice = make_user(email="alice@test.local", role=UserRole.employee)
    bob = make_user(email="bob@test.local", role=UserRole.employee)
    charlie = make_user(email="charlie@test.local", role=UserRole.client)
    g = group_svc.create_group(
        db,
        actor=admin,
        name="team",
        description=None,
        is_company_inbox=False,
    )
    db.commit()
    for u in (alice, bob, charlie):
        group_svc.add_member(db, actor=admin, group=g, user=u)
    db.commit()

    # Recompute creates the initial shared_group rows.
    connection_svc.recompute_shared_group_connections_for_user(db, user=alice)
    connection_svc.recompute_shared_group_connections_for_user(db, user=bob)
    connection_svc.recompute_shared_group_connections_for_user(
        db, user=charlie
    )
    db.commit()

    rows_before = {
        (r.client_user_id, r.employee_user_id, r.source.value)
        for r in db.query(ClientEmployeeConnection).all()
    }
    assert (charlie.id, alice.id, "shared_group") in rows_before
    assert (charlie.id, bob.id, "shared_group") in rows_before

    um_svc.update_user(db, actor=admin, target=alice, role=UserRole.client)
    db.commit()

    rows_after = {
        (r.client_user_id, r.employee_user_id, r.source.value)
        for r in db.query(ClientEmployeeConnection).all()
    }
    # Charlie↔Alice gone (Alice no longer on employee slot)
    assert (charlie.id, alice.id, "shared_group") not in rows_after
    # Charlie↔Bob untouched
    assert (charlie.id, bob.id, "shared_group") in rows_after
    # Alice↔Bob freshly created (Alice is client now, shares group with Bob)
    assert (alice.id, bob.id, "shared_group") in rows_after


def test_update_user_without_role_change_leaves_connections(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    alice = make_user(email="alice@test.local", role=UserRole.employee)
    bob = make_user(email="bob@test.local", role=UserRole.client)
    connection_svc.record_invite_connection(db, inviter=alice, invitee=bob)
    db.commit()

    um_svc.update_user(
        db, actor=admin, target=alice, display_name="Alice the Renamed"
    )
    db.commit()

    assert (
        db.query(ClientEmployeeConnection)
        .filter(
            ClientEmployeeConnection.employee_user_id == alice.id,
            ClientEmployeeConnection.client_user_id == bob.id,
        )
        .count()
        == 1
    )


@pytest.mark.asyncio
async def test_admin_patch_user_role_triggers_cleanup_end_to_end(
    make_user, db, client, login_as
):
    """End-to-end via the admin API: PATCH /api/admin/users/{id} with a
    role change runs the cleanup."""
    make_user(
        email="admin@test.local",
        role=UserRole.admin,
        password="Pass12345678!",
    )
    alice = make_user(email="alice@test.local", role=UserRole.employee)
    bob = make_user(email="bob@test.local", role=UserRole.client)
    connection_svc.record_invite_connection(db, inviter=alice, invitee=bob)
    db.commit()

    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.patch(
        f"/api/admin/users/{alice.id}",
        json={"role": "client"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    db.expire_all()
    assert (
        db.query(ClientEmployeeConnection)
        .filter(ClientEmployeeConnection.employee_user_id == alice.id)
        .count()
        == 0
    )
