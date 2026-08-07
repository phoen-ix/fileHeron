"""The mandatory-2FA gate must actually block credential-issuing routes.

Regression cover for a 2026-07-30 audit finding. main.py mounted the WHOLE
account module ungated, with the comment "/me + /2fa/* must be reachable" -
which also exempted POST /api/account/api-tokens and POST /api/account/invite.
Because require_2fa_complete deliberately short-circuits for `auth_via ==
"api_token"`, a user the policy covered could log in with a password, mint a
token from the ungated route, and then use that token on every gated route in
the app. Mandatory 2FA was advisory and the documented "no admin escape" was
false.

These tests are BEHAVIOURAL on purpose. The obvious alternative - introspecting
route dependencies - cannot see dependencies attached at include_router() time,
and FastAPI 0.141 additionally collapses each include into an opaque
_IncludedRouter (see tests/_route_helpers.py). A guard built on introspection
would have silently stopped checking; a real request cannot.
"""
from __future__ import annotations

import pytest

from app.models.user import UserRole
from app.services import settings as settings_svc


@pytest.fixture
def require_2fa_for_everyone(db):
    """Turn on the policy for every role, so any test user is covered."""
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.TWOFA_REQUIRED_ROLES,
        value='["admin", "employee", "client"]',
        actor=None,
    )
    db.commit()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_token_minting_is_blocked_without_2fa(
    client, make_user, login_as, require_2fa_for_everyone
):
    """The bypass itself. If this route is reachable, the whole gate is."""
    make_user(email="emp@test.local", role=UserRole.employee)
    token, _ = await login_as("emp@test.local", "TestPassword123!")

    resp = await client.post(
        "/api/account/api-tokens",
        json={"name": "escape-hatch", "scopes": None, "expires_at": None, "password": "TestPassword123!"},
        headers=_auth(token),
    )

    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "TWOFA_SETUP_REQUIRED"


@pytest.mark.asyncio
async def test_invite_is_blocked_without_2fa(
    client, make_user, login_as, require_2fa_for_everyone
):
    make_user(email="emp@test.local", role=UserRole.employee)
    token, _ = await login_as("emp@test.local", "TestPassword123!")

    resp = await client.post(
        "/api/account/invite",
        json={"email": "x@test.local", "display_name_hint": "X"},
        headers=_auth(token),
    )

    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "TWOFA_SETUP_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/account/me"),
        ("GET", "/api/account/2fa/status"),
    ],
)
async def test_setup_routes_stay_reachable(
    client, make_user, login_as, require_2fa_for_everyone, method, path
):
    """Control: a blocked user must still be able to learn the requirement and
    enrol, or the policy would lock everyone out permanently."""
    make_user(email="emp@test.local", role=UserRole.employee)
    token, _ = await login_as("emp@test.local", "TestPassword123!")

    resp = await client.request(method, path, headers=_auth(token))

    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_totp_setup_stays_reachable(
    client, make_user, login_as, require_2fa_for_everyone
):
    make_user(email="emp@test.local", role=UserRole.employee)
    token, _ = await login_as("emp@test.local", "TestPassword123!")

    resp = await client.post("/api/account/2fa/setup", headers=_auth(token))

    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_token_minting_works_once_policy_is_satisfied(
    client, db, make_user, login_as
):
    """Control: with no policy in force, minting still works - so the tests
    above fail for the right reason."""
    make_user(email="emp@test.local", role=UserRole.employee)
    token, _ = await login_as("emp@test.local", "TestPassword123!")

    resp = await client.post(
        "/api/account/api-tokens",
        json={"name": "legit", "scopes": None, "expires_at": None, "password": "TestPassword123!"},
        headers=_auth(token),
    )

    assert resp.status_code == 201, resp.text
