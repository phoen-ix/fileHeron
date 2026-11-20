"""A public link's download limit can be changed, and its URL stays the same.

Until v2.32.0 the only way to give a link more downloads was revoke + recreate,
which issues a NEW URL and kills the one already mailed - on 2026-11-20 the
owner of a link a mail scanner had spent did exactly that, and raised the
SHARE's own limit first, which never touched the link. Semantics mirror
`share.update_share_limit` (test_share_download_limit.py).
"""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import public_link as public_link_svc

PW = "Pass12345678!"


@pytest.fixture
def linked(db, make_user):
    owner = make_user(email="owner@test.local", role=UserRole.employee, password=PW)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    created = public_link_svc.create_link(
        db, share=sh, actor=owner, password=None, download_limit=2, notify_on_download=False
    )
    db.commit()
    return owner, sh, created.record


async def _patch(client, login_as, email, share_id, body):
    token, _ = await login_as(email, PW)
    return await client.patch(
        f"/api/shares/{share_id}/public-link",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_raising_a_spent_link_reopens_it_under_the_same_url(client, db, login_as, linked):
    owner, sh, link = linked
    url_before = public_link_svc.stored_url(db, link)
    link.downloads_remaining = 0
    db.commit()

    r = await _patch(client, login_as, owner.email, sh.id, {"download_limit": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["download_limit"], body["downloads_remaining"]) == (5, 3)
    assert body["url"] == url_before and body["id"] == link.id

    row = db.query(AuditLog).filter(
        AuditLog.event_type == AuditEventType.public_link_updated
    ).one()
    assert row.target_id == link.id


@pytest.mark.asyncio
async def test_lowering_below_what_was_used_leaves_none(client, db, login_as, linked):
    owner, sh, link = linked
    link.downloads_remaining = 1  # one of two used
    db.commit()
    r = await _patch(client, login_as, owner.email, sh.id, {"download_limit": 1})
    assert (r.json()["download_limit"], r.json()["downloads_remaining"]) == (1, 0)


@pytest.mark.asyncio
async def test_clearing_makes_it_unlimited(client, db, login_as, linked):
    owner, sh, _ = linked
    r = await _patch(client, login_as, owner.email, sh.id, {"download_limit_clear": True})
    assert (r.json()["download_limit"], r.json()["downloads_remaining"]) == (None, None)


@pytest.mark.asyncio
async def test_the_request_is_checked(client, db, login_as, make_user, linked):
    owner, sh, link = linked
    r = await _patch(
        client, login_as, owner.email, sh.id, {"download_limit": 3, "download_limit_clear": True}
    )
    assert (r.status_code, r.json()["code"]) == (400, "INVALID_INPUT")
    r = await _patch(client, login_as, owner.email, sh.id, {"download_limit": 0})
    assert r.status_code == 422

    other = make_user(email="other@test.local", role=UserRole.employee, password=PW)
    r = await _patch(client, login_as, other.email, sh.id, {"download_limit": 9})
    assert r.status_code == 403

    public_link_svc.revoke(db, actor=owner, link=link)
    db.commit()
    r = await _patch(client, login_as, owner.email, sh.id, {"download_limit": 9})
    assert (r.status_code, r.json()["code"]) == (404, "PUBLIC_LINK_NOT_FOUND")


@pytest.mark.asyncio
async def test_an_approved_shares_link_is_widened_only_by_an_admin(
    client, db, login_as, make_user, linked
):
    """Raising or removing the limit hands the anonymous URL to more downloads
    than the approver saw; lowering it never needs anyone."""
    owner, sh, link = linked
    sh.approval_was_required = True
    db.commit()

    for body in ({"download_limit": 5}, {"download_limit_clear": True}):
        r = await _patch(client, login_as, owner.email, sh.id, body)
        assert (r.status_code, r.json()["code"]) == (409, "APPROVAL_REQUIRED"), body
    db.refresh(link)
    assert (link.download_limit, link.downloads_remaining) == (2, 2)

    r = await _patch(client, login_as, owner.email, sh.id, {"download_limit": 1})
    assert r.status_code == 200, r.text

    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    r = await _patch(client, login_as, "admin@test.local", sh.id, {"download_limit": 5})
    assert r.status_code == 200, r.text
    assert r.json()["downloads_remaining"] == 5


@pytest.mark.asyncio
async def test_a_pending_shares_link_is_widened_only_by_an_admin(client, db, login_as, linked):
    owner, sh, _ = linked
    sh.state = ShareState.pending_approval
    db.commit()
    r = await _patch(client, login_as, owner.email, sh.id, {"download_limit": 5})
    assert (r.status_code, r.json()["code"]) == (409, "APPROVAL_REQUIRED")
