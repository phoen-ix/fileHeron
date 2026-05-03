"""/api/users/* — recipient resolution + search.

Phase 4 model:
- /api/users/lookup (employee/admin only) is the explicit-email-resolve flow,
  used by the Phase 3b RecipientPicker. Kept for back-compat with the
  current SPA; the new picker uses /search instead.
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
from ..middleware.errors import AppError
from ..models.client_employee_connection import (
    ClientEmployeeConnection,
    ConnectionSource,
)
from ..models.user import User, UserRole
from ..schemas.user import (
    ConnectionItem,
    ConnectionListResponse,
    UserLookupRequest,
    UserLookupResponse,
    UserSearchItem,
    UserSearchResponse,
)
from ..services import connection as connection_svc
from ..utils.crypto import normalize_email

router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("/lookup", response_model=UserLookupResponse)
def lookup(
    payload: UserLookupRequest,
    me: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> UserLookupResponse:
    """Email-anchored resolve (employee/admin only). Phase 4 keeps this for
    the existing SPA; new code should use /search."""
    if me.role not in (UserRole.admin, UserRole.employee):
        raise AppError(403, "FORBIDDEN", "Only employees and admins can look up users.")

    em_hash = normalize_email(payload.email)
    user = db.query(User).filter(User.email == em_hash).one_or_none()
    if user is None or user.is_disabled:
        raise AppError(404, "USER_NOT_FOUND", "No active user with that email.")
    if user.id == me.id:
        raise AppError(400, "SELF_LOOKUP", "That's you — pick a different recipient.")

    return UserLookupResponse(
        user_id=user.id, display_name=user.display_name, email=user.email
    )


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
    filtered by `q` (substring match on display_name + email_hint)."""
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
