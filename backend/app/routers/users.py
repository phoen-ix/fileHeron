"""/api/users/* - recipient resolution + search.

Phase 4 model:
- /api/users/search?q= returns the union of users I'm allowed to target,
  filtered by a display-name / email-hint substring. Scope:
    * admin → all active users (clients + employees + admins)
    * employee → connected clients + all employees + admins
    * client → connected employees only (no client enumeration)
- /api/users/me/connections returns my current connection list.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..dependencies import get_actor, get_db
from ..models.client_employee_connection import (
    ClientEmployeeConnection,
    ConnectionSource,
)
from ..models.user import User, UserRole
from ..schemas.user import (
    ConnectionItem,
    ConnectionListResponse,
    UserSearchItem,
    UserSearchResponse,
)
from ..services import connection as connection_svc

router = APIRouter(prefix="/api/users", tags=["users"])


def _to_search_item(u: User) -> UserSearchItem:
    return UserSearchItem(
        user_id=u.id,
        display_name=u.display_name,
        email=u.email,
        role=u.role.value,
    )


def _filter_by_q(users: list[User], q: str) -> list[User]:
    if not q:
        return users
    needle = q.lower().strip()
    return [
        u
        for u in users
        if needle in u.display_name.lower() or needle in u.email.lower()
    ]


@router.get("/search", response_model=UserSearchResponse)
def search(
    q: str = Query("", max_length=120),
    me: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> UserSearchResponse:
    """Return the union of users the caller can address as a recipient,
    filtered by `q` (substring match on display_name + email)."""
    if me.role == UserRole.admin:
        candidates = (
            db.query(User)
            .filter(User.is_disabled.is_(False), User.id != me.id)
            .order_by(User.display_name)
            .all()
        )
    elif me.role == UserRole.employee:
        # Employees see all employees/admins (small team, no privacy issue)
        # plus their connected clients.
        non_clients = (
            db.query(User)
            .filter(
                User.is_disabled.is_(False),
                User.id != me.id,
                User.role.in_([UserRole.employee, UserRole.admin]),
            )
            .all()
        )
        connected_clients = connection_svc.list_clients_visible_to(db, viewer=me)
        candidates = non_clients + connected_clients
    elif me.role == UserRole.client:
        candidates = connection_svc.list_employees_visible_to(db, viewer=me)
    else:
        candidates = []

    filtered = _filter_by_q(candidates, q)
    # De-dupe in case a viewer would have matched in two paths.
    seen: set[int] = set()
    unique: list[User] = []
    for u in filtered:
        if u.id in seen:
            continue
        seen.add(u.id)
        unique.append(u)

    return UserSearchResponse(items=[_to_search_item(u) for u in unique[:50]])


@router.get("/me/connections", response_model=ConnectionListResponse)
def my_connections(
    me: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> ConnectionListResponse:
    """List the people I'm connected to (any source). For clients →
    employees; for employees → clients. Admin returns []. The same
    counterparty appears once with all matching sources merged."""
    if me.role == UserRole.admin:
        return ConnectionListResponse(items=[])

    # Pull all connection rows on either side; group by counterparty + role.
    if me.role == UserRole.client:
        rows = (
            db.query(ClientEmployeeConnection)
            .filter(ClientEmployeeConnection.client_user_id == me.id)
            .all()
        )
        counterparty_attr = "employee_user_id"
    else:
        rows = (
            db.query(ClientEmployeeConnection)
            .filter(ClientEmployeeConnection.employee_user_id == me.id)
            .all()
        )
        counterparty_attr = "client_user_id"

    sources_by_user: dict[int, list[ConnectionSource]] = {}
    for r in rows:
        cid = getattr(r, counterparty_attr)
        sources_by_user.setdefault(cid, []).append(r.source)

    if not sources_by_user:
        return ConnectionListResponse(items=[])

    users = (
        db.query(User)
        .filter(User.id.in_(list(sources_by_user.keys())), User.is_disabled.is_(False))
        .all()
    )
    items = [
        ConnectionItem(
            user_id=u.id,
            display_name=u.display_name,
            email=u.email,
            role=u.role.value,
            sources=[s.value for s in sources_by_user[u.id]],
        )
        for u in users
    ]
    return ConnectionListResponse(items=items)
