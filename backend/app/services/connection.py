"""Client ↔ employee connections.

A connection exists when a client and an employee can address each other in
shares. Two flavors live in the same table, distinguished by `source`:

- `invite` - set when an employee/admin invites a client. **Sticky:** stays
  forever (until explicit erasure flow), since the invite is the original
  trust anchor.
- `shared_group` - set whenever a client and an employee share at least one
  group. **Dynamic:** disappears as soon as the last shared group is left.

Both rows can coexist for the same pair; the recipient-search ACL is the
union (any source = connected).

The two callers are:
- `consume_invite` flow → `record_invite_connection`.
- group member add/remove → `recompute_shared_group_connections_for_user`.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.client_employee_connection import (
    ClientEmployeeConnection,
    ConnectionSource,
)
from ..models.group_member import GroupMember
from ..models.user import User, UserRole


def _classify_pair(a: User, b: User) -> tuple[int, int] | None:
    """Return (client_id, employee_id) if (a,b) is a client↔employee pair,
    else None. Two clients or two employees do NOT form a connection."""
    if a.role == UserRole.client and b.role in (UserRole.employee, UserRole.admin):
        return a.id, b.id
    if b.role == UserRole.client and a.role in (UserRole.employee, UserRole.admin):
        return b.id, a.id
    return None


def record_invite_connection(
    db: Session, *, inviter: User, invitee: User
) -> None:
    """Record an invite-source connection between inviter and invitee.

    Idempotent: returns silently if the row already exists. Skips silently
    if the pair is not a client↔employee combination (admin invites
    employee, etc. - no connection needed)."""
    pair = _classify_pair(inviter, invitee)
    if pair is None:
        return
    client_id, employee_id = pair
    existing = (
        db.query(ClientEmployeeConnection)
        .filter(
            ClientEmployeeConnection.client_user_id == client_id,
            ClientEmployeeConnection.employee_user_id == employee_id,
            ClientEmployeeConnection.source == ConnectionSource.invite,
        )
        .one_or_none()
    )
    if existing is not None:
        return
    db.add(
        ClientEmployeeConnection(
            client_user_id=client_id,
            employee_user_id=employee_id,
            source=ConnectionSource.invite,
        )
    )
    db.flush()


def _users_sharing_a_group_with(db: Session, user_id: int) -> list[User]:
    """Distinct users that share ≥ 1 group with user_id (excluding user_id)."""
    gm_self = GroupMember.__table__.alias("gm1")
    gm_other = GroupMember.__table__.alias("gm2")
    rows = (
        db.execute(
            select(User)
            .join(gm_other, gm_other.c.user_id == User.id)
            .join(gm_self, gm_self.c.group_id == gm_other.c.group_id)
            .where(gm_self.c.user_id == user_id, gm_other.c.user_id != user_id)
            .distinct()
        )
        .scalars()
        .all()
    )
    return list(rows)


def recompute_shared_group_connections_for_user(
    db: Session, *, user: User
) -> None:
    """Reconcile this user's `shared_group` connection rows with their
    current group memberships.

    - Adds rows for each client↔employee pair where one side is `user`
      and they share at least one group.
    - Removes rows where the pair no longer shares any group.

    Invite-source rows are never touched."""
    if user.role not in (UserRole.client, UserRole.employee, UserRole.admin):
        return

    sharing_users = _users_sharing_a_group_with(db, user.id)

    # Two queries, then set arithmetic. Adding N members to a group used to
    # cost O(N x peers) round-trips because each pair was checked on its own.
    existing_rows = (
        db.query(ClientEmployeeConnection)
        .filter(
            ClientEmployeeConnection.source == ConnectionSource.shared_group,
            (ClientEmployeeConnection.client_user_id == user.id)
            | (ClientEmployeeConnection.employee_user_id == user.id),
        )
        .all()
    )
    existing_pairs = {
        (r.client_user_id, r.employee_user_id): r for r in existing_rows
    }

    wanted_pairs: set[tuple[int, int]] = set()
    for other in sharing_users:
        pair = _classify_pair(user, other)
        if pair is not None:
            wanted_pairs.add(pair)

    for pair in wanted_pairs - set(existing_pairs):
        db.add(
            ClientEmployeeConnection(
                client_user_id=pair[0],
                employee_user_id=pair[1],
                source=ConnectionSource.shared_group,
            )
        )

    # Prune rows whose pair no longer shares a group. Every row here involves
    # `user`, so "still shares a group" is exactly "the counterparty is in
    # sharing_users" - no per-pair query needed.
    for pair, row in existing_pairs.items():
        if pair not in wanted_pairs:
            db.delete(row)
    db.flush()


def cleanup_connections_for_role_change(
    db: Session, *, target: User, old_role: UserRole
) -> int:
    """Drop `ClientEmployeeConnection` rows that no longer make sense for
    `target`'s new role and rebuild any `shared_group` rows the new slot
    should have.

    The connection table only describes client↔(employee|admin) pairs;
    its column names (`client_user_id`, `employee_user_id`) lock each
    row to a specific slot. When `target.role` flips between client and
    non-client, every row that put `target` in the old slot is now a
    lie. We delete those rows (both invite + shared_group) and rerun
    `recompute_shared_group_connections_for_user` so any group-derived
    pairings appropriate for the new role get repopulated. Invite-source
    rows for the new role can't be reconstructed (they only exist when an
    actual invite happened) - that's correct, the trust anchor is gone.

    Slot-preserving transitions (employee↔admin) are no-ops.

    Caller commits. Returns the number of rows deleted (handy for the
    audit metadata)."""
    was_client = old_role == UserRole.client
    is_client = target.role == UserRole.client
    if was_client == is_client:
        return 0

    if was_client:
        q = db.query(ClientEmployeeConnection).filter(
            ClientEmployeeConnection.client_user_id == target.id
        )
    else:
        q = db.query(ClientEmployeeConnection).filter(
            ClientEmployeeConnection.employee_user_id == target.id
        )
    deleted = q.delete(synchronize_session=False)
    db.flush()

    recompute_shared_group_connections_for_user(db, user=target)
    return deleted


def list_employees_visible_to(
    db: Session, *, viewer: User
) -> list[User]:
    """For a client viewer: the set of employees they can target as
    recipients (= union of invite + shared_group connections, distinct)."""
    if viewer.role != UserRole.client:
        return []
    rows = (
        db.query(ClientEmployeeConnection.employee_user_id)
        .filter(ClientEmployeeConnection.client_user_id == viewer.id)
        .distinct()
        .all()
    )
    employee_ids = [r[0] for r in rows]
    if not employee_ids:
        return []
    return (
        db.query(User)
        .filter(User.id.in_(employee_ids), User.is_disabled.is_(False))
        .all()
    )


def list_clients_visible_to(
    db: Session, *, viewer: User
) -> list[User]:
    """For an employee/admin viewer: every active client they're connected
    to (clients table is the universe; admins see all clients)."""
    if viewer.role == UserRole.admin:
        return (
            db.query(User)
            .filter(User.role == UserRole.client, User.is_disabled.is_(False))
            .all()
        )
    if viewer.role != UserRole.employee:
        return []
    rows = (
        db.query(ClientEmployeeConnection.client_user_id)
        .filter(ClientEmployeeConnection.employee_user_id == viewer.id)
        .distinct()
        .all()
    )
    client_ids = [r[0] for r in rows]
    if not client_ids:
        return []
    return (
        db.query(User)
        .filter(User.id.in_(client_ids), User.is_disabled.is_(False))
        .all()
    )
