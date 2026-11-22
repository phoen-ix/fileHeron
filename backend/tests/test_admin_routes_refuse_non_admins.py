"""Every admin route refuses an employee and a client - all of them.

The admin console's data (every user's address, every group's members, the
audit log, the settings) is guarded one route at a time by
`Depends(get_current_admin)`, and the only check that nobody forgot one was a
hand-written list of GETs in the e2e sweep. `GET /api/groups/{id}` - every
member's email and role - was on no list and in no test (audit 2026-11-22).

Generic on purpose: it walks the routes the app actually serves and CALLS each
one, as an employee and as a client. A new admin route is covered the day it
is added; a route under `/api/admin` that does not ask for an admin fails the
first test, by name.
"""
from __future__ import annotations

import re

import pytest

from app.config import settings
from app.dependencies import get_current_admin
from app.main import app
from app.models.user import User, UserRole
from app.services import jwt_session
from app.utils.crypto import argon2_hash

from ._route_helpers import iter_effective_routes

# Under /api/admin without the admin dependency, each with the reason. An
# EventSource cannot send an Authorization header, so the live streams take a
# signed `?token=` that only an admin can mint (docs/engineering/mail.md).
NOT_BY_HEADER = {
    "GET /api/admin/system/stream": "signed ?token=, minted by an admin-only route",
}


def _asks_for_an_admin(dependant) -> bool:
    return dependant.call is get_current_admin or any(
        _asks_for_an_admin(d) for d in dependant.dependencies
    )


ROUTES = sorted(
    (f"{method} {path}", _asks_for_an_admin(dependant))
    for path, methods, dependant in iter_effective_routes(app)
    for method in methods
    if method not in ("HEAD", "OPTIONS")
)
ADMIN_ONLY = [key for key, admin in ROUTES if admin]


def test_the_walk_sees_the_app():
    # Vacuity: `iter_effective_routes` reads a FastAPI internal.
    assert len(ROUTES) > 250, len(ROUTES)
    assert len(ADMIN_ONLY) > 120, len(ADMIN_ONLY)
    assert "GET /api/admin/users" in ADMIN_ONLY
    # Admin-only routes live outside /api/admin too: group management.
    assert "GET /api/groups/{group_id}" in ADMIN_ONLY
    assert "GET /api/groups/recipient-targets" not in ADMIN_ONLY


def test_every_route_under_api_admin_asks_for_an_admin():
    ungated = [key for key, admin in ROUTES if " /api/admin" in key and not admin]
    assert sorted(ungated) == sorted(NOT_BY_HEADER), (
        "a route under /api/admin does not depend on get_current_admin; gate "
        "it, or list it in NOT_BY_HEADER with how it is authenticated instead"
    )


def _request(key: str) -> tuple[str, str]:
    method, path = key.split(" ", 1)
    return method, re.sub(r"\{[^}]+\}", "1", path)


@pytest.mark.asyncio
async def test_every_admin_route_refuses_an_employee_and_a_client(db, client):
    headers = {}
    for role in (UserRole.employee, UserRole.client):
        user = User(
            email=f"{role.value}@test.local",
            password_hash=argon2_hash("TestPassword123!"),
            display_name=role.value,
            role=role,
            email_verified=True,
        )
        db.add(user)
        db.commit()
        token, _ = jwt_session.create_access_token(user.id, settings)
        headers[role.value] = {"Authorization": f"Bearer {token}"}

    let_through: list[str] = []
    for key in ADMIN_ONLY:
        method, url = _request(key)
        for role, auth in headers.items():
            body = {} if method in ("POST", "PUT", "PATCH", "DELETE") else None
            r = await client.request(method, url, headers=auth, json=body)
            if r.status_code != 403 or r.json().get("code") != "FORBIDDEN":
                let_through.append(f"{role}: {key} -> {r.status_code} {r.text[:80]}")
    assert let_through == []

    # And the routes that are not gated by header refuse whoever has no signed
    # token - a signed-in employee included, whose bearer counts for nothing.
    for key in NOT_BY_HEADER:
        method, url = _request(key)
        assert (await client.request(method, url)).status_code == 401, key
        forged = await client.request(method, f"{url}?token=nonsense", headers=headers["employee"])
        assert (forged.status_code, forged.json()["code"]) == (401, "INVALID_SSE_TOKEN"), key
