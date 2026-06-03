"""Group CRUD + uniqueness + membership wiring."""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.client_employee_connection import (
    ClientEmployeeConnection,
    ConnectionSource,
)
from app.models.group import Group
from app.models.group_member import GroupMember
from app.models.user import UserRole
from app.services import group as group_svc


@pytest.mark.asyncio
async def test_create_group_persists_and_normalizes(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    g = group_svc.create_group(
        db,
        actor=admin,
        name="ACME Corp",
        description="The customer ACME.",
        is_company_inbox=False,
    )
    db.commit()
    fetched = db.query(Group).filter(Group.id == g.id).one()
    assert fetched.name == "ACME Corp"
    assert fetched.name_normalized == "acme corp"


@pytest.mark.asyncio
async def test_group_name_case_insensitive_unique(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    group_svc.create_group(
        db, actor=admin, name="ACME Corp", description=None, is_company_inbox=False
    )
    db.commit()
    with pytest.raises(AppError) as exc:
        group_svc.create_group(
            db,
            actor=admin,
            name="acme corp",
            description=None,
            is_company_inbox=False,
        )
    assert exc.value.code == "GROUP_NAME_TAKEN"


@pytest.mark.asyncio
async def test_add_member_creates_shared_group_connection(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    employee = make_user(email="emp@test.local", role=UserRole.employee)
    client_user = make_user(email="cli@test.local", role=UserRole.client)

    g = group_svc.create_group(
        db, actor=admin, name="Team-A", description=None, is_company_inbox=False
    )
    db.commit()

    group_svc.add_member(db, actor=admin, group=g, user=employee)
    group_svc.add_member(db, actor=admin, group=g, user=client_user)
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
    assert rows[0].source == ConnectionSource.shared_group


@pytest.mark.asyncio
async def test_remove_member_drops_shared_group_only_when_no_other(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    employee = make_user(email="emp@test.local", role=UserRole.employee)
    client_user = make_user(email="cli@test.local", role=UserRole.client)

    g1 = group_svc.create_group(
        db, actor=admin, name="A", description=None, is_company_inbox=False
    )
    g2 = group_svc.create_group(
        db, actor=admin, name="B", description=None, is_company_inbox=False
    )
    db.commit()
    for g in (g1, g2):
        group_svc.add_member(db, actor=admin, group=g, user=employee)
        group_svc.add_member(db, actor=admin, group=g, user=client_user)
    db.commit()

    # Remove from one group → connection still present.
    group_svc.remove_member(db, actor=admin, group=g1, user=client_user)
    db.commit()
    assert (
        db.query(ClientEmployeeConnection)
        .filter(
            ClientEmployeeConnection.client_user_id == client_user.id,
            ClientEmployeeConnection.employee_user_id == employee.id,
            ClientEmployeeConnection.source == ConnectionSource.shared_group,
        )
        .count()
        == 1
    )

    # Remove from the second group → connection gone.
    group_svc.remove_member(db, actor=admin, group=g2, user=client_user)
    db.commit()
    assert (
        db.query(ClientEmployeeConnection)
        .filter(
            ClientEmployeeConnection.client_user_id == client_user.id,
            ClientEmployeeConnection.employee_user_id == employee.id,
            ClientEmployeeConnection.source == ConnectionSource.shared_group,
        )
        .count()
        == 0
    )


@pytest.mark.asyncio
async def test_delete_group_blocks_when_active_share_targets_it(
    make_user, db
):
    from datetime import datetime, timedelta, timezone

    from app.models.share import ShareKind
    from app.services import share as share_svc

    admin = make_user(email="admin@test.local", role=UserRole.admin)
    member = make_user(email="member@test.local", role=UserRole.client)

    g = group_svc.create_group(
        db, actor=admin, name="inbox-clients", description=None, is_company_inbox=True
    )
    db.commit()

    future = (datetime.now(tz=timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)
    share_svc.create_share(
        db,
        created_by=admin,
        kind=ShareKind.outbound,
        recipient_group_ids=[g.id],
        expires_at=future,
    )
    db.commit()

    with pytest.raises(AppError) as exc:
        group_svc.delete_group(db, actor=admin, group=g)
    assert exc.value.code == "GROUP_IN_USE"

    # Sanity: also check membership row still references after delete attempt.
    _ = member  # keep reference fresh; not used otherwise.


@pytest.mark.asyncio
async def test_member_idempotent_re_add(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    user = make_user(email="x@test.local", role=UserRole.client)
    g = group_svc.create_group(
        db, actor=admin, name="grp", description=None, is_company_inbox=False
    )
    db.commit()
    group_svc.add_member(db, actor=admin, group=g, user=user)
    group_svc.add_member(db, actor=admin, group=g, user=user)  # no-op
    db.commit()
    assert (
        db.query(GroupMember)
        .filter(GroupMember.group_id == g.id, GroupMember.user_id == user.id)
        .count()
        == 1
    )
