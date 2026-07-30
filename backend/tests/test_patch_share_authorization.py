"""PATCH /api/shares/{id} must authorize before it branches.

Regression cover for a 2026-07-30 audit finding: the owner-or-admin check lived
only inside update_share_expiry / update_share_limit, so a PATCH whose body
changed nothing called neither and still fell through to the serializer -
turning the endpoint into a share-metadata read for any authenticated caller.
"""
from __future__ import annotations

import pytest

from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole


@pytest.fixture
def share_of(db):
    def _make(owner_id: int, subject: str = "Quarterly payroll") -> Share:
        s = Share(
            created_by_id=owner_id,
            kind=ShareKind.outbound,
            state=ShareState.active,
            subject=subject,
        )
        db.add(s)
        db.commit()
        return s

    return _make


async def _patch(client, token: str, share_id: str, body: dict):
    return await client.patch(
        f"/api/shares/{share_id}",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_empty_patch_by_a_stranger_is_refused(
    client, make_user, login_as, share_of
):
    """The leak itself: a no-op body used to skip every authorization branch."""
    owner = make_user(email="owner@test.local", role=UserRole.employee)
    make_user(email="stranger@test.local", role=UserRole.employee)
    share = share_of(owner.id)
    token, _ = await login_as("stranger@test.local", "TestPassword123!")

    resp = await _patch(client, token, share.id, {})

    assert resp.status_code == 403, resp.text
    # And the subject must not have leaked in the body.
    assert "Quarterly payroll" not in resp.text


@pytest.mark.asyncio
async def test_substantive_patch_by_a_stranger_is_refused(
    client, make_user, login_as, share_of
):
    """Control: the pre-existing service-level check still holds."""
    owner = make_user(email="owner@test.local", role=UserRole.employee)
    make_user(email="stranger@test.local", role=UserRole.employee)
    share = share_of(owner.id)
    token, _ = await login_as("stranger@test.local", "TestPassword123!")

    resp = await _patch(client, token, share.id, {"expires_at_clear": True})

    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_owner_can_still_patch(client, make_user, login_as, share_of):
    """Control: the owner workflow must keep working."""
    owner = make_user(email="owner@test.local", role=UserRole.employee)
    share = share_of(owner.id)
    token, _ = await login_as("owner@test.local", "TestPassword123!")

    resp = await _patch(client, token, share.id, {"expires_at_clear": True})

    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_admin_can_still_patch(client, make_user, login_as, share_of):
    owner = make_user(email="owner@test.local", role=UserRole.employee)
    make_user(email="boss@test.local", role=UserRole.admin)
    share = share_of(owner.id)
    token, _ = await login_as("boss@test.local", "TestPassword123!")

    resp = await _patch(client, token, share.id, {"expires_at_clear": True})

    assert resp.status_code == 200, resp.text
