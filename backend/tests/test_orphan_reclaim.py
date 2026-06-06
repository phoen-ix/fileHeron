"""Orphaned-file reclaim: revoke stamps terminated_at; the grace-window cron
frees bytes + quota; admins can reclaim immediately + filter orphans.

An orphan = a file still on disk in state clean/ready_unscanned whose parent
share is revoked/deleted (bytes still counting quota, invisible in the
active-only Sent/Received view).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import settings as settings_svc


def _now():
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _make_share(db, owner_id, *, state=ShareState.revoked, terminated_ago_days=None):
    s = Share(created_by_id=owner_id, kind=ShareKind.outbound, state=state)
    if terminated_ago_days is not None:
        s.terminated_at = _now() - timedelta(days=terminated_ago_days)
    db.add(s)
    db.flush()
    return s


def _make_file(db, share_id, owner_id, *, state=FileState.clean, size=1000, name="f.bin"):
    f = File(
        share_id=share_id,
        original_filename=name,
        mime_type="application/octet-stream",
        size_bytes=size,
        storage_path="/tmp/fileheron-test/files/does-not-exist.bin",
        state=state,
        uploaded_by_id=owner_id,
    )
    db.add(f)
    db.flush()
    return f


# ---- revoke stamps terminated_at -----------------------------------------


def test_revoke_share_stamps_terminated_at(make_user, db):
    from app.services import share as share_svc

    owner = make_user(email="hr@test.local", role=UserRole.admin)
    recipient = make_user(email="cli@test.local", role=UserRole.client)
    share = share_svc.create_share(
        db,
        created_by=owner,
        kind=ShareKind.outbound,
        recipient_user_ids=[recipient.id],
        expires_at=_now() + timedelta(hours=1),
    )
    db.commit()
    assert share.terminated_at is None
    share_svc.revoke_share(db, user=owner, share=share)
    db.commit()
    db.refresh(share)
    assert share.state == ShareState.revoked
    assert share.terminated_at is not None


# ---- the reclaim cron ------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_reclaims_orphan_past_grace(make_user, db):
    from app.workers.reclaim_orphaned_files import reclaim_orphaned_files

    owner = make_user(email="u@test.local", role=UserRole.employee)
    share = _make_share(db, owner.id, state=ShareState.revoked, terminated_ago_days=10)
    f = _make_file(db, share.id, owner.id, state=FileState.clean)
    db.commit()

    res = await reclaim_orphaned_files(None)
    assert res["reclaimed"] == 1
    db.refresh(f)
    assert f.state == FileState.deleted


@pytest.mark.asyncio
async def test_cron_skips_within_grace(make_user, db):
    from app.workers.reclaim_orphaned_files import reclaim_orphaned_files

    owner = make_user(email="u@test.local", role=UserRole.employee)
    share = _make_share(db, owner.id, state=ShareState.revoked, terminated_ago_days=1)
    f = _make_file(db, share.id, owner.id, state=FileState.clean)
    db.commit()

    res = await reclaim_orphaned_files(None)
    assert res["reclaimed"] == 0
    db.refresh(f)
    assert f.state == FileState.clean


@pytest.mark.asyncio
async def test_cron_skips_quarantined_and_active(make_user, db):
    from app.workers.reclaim_orphaned_files import reclaim_orphaned_files

    owner = make_user(email="u@test.local", role=UserRole.employee)
    # Quarantined file (infected) under a revoked share - must NOT be swept.
    q_share = _make_share(db, owner.id, state=ShareState.revoked, terminated_ago_days=10)
    qf = _make_file(db, q_share.id, owner.id, state=FileState.infected)
    # Active share's clean file - must NOT be swept.
    a_share = _make_share(db, owner.id, state=ShareState.active)
    af = _make_file(db, a_share.id, owner.id, state=FileState.clean)
    db.commit()

    res = await reclaim_orphaned_files(None)
    assert res["reclaimed"] == 0
    db.refresh(qf)
    db.refresh(af)
    assert qf.state == FileState.infected
    assert af.state == FileState.clean


@pytest.mark.asyncio
async def test_cron_disabled_at_zero(make_user, db):
    from app.workers.reclaim_orphaned_files import reclaim_orphaned_files

    settings_svc.set_value(
        db, key=settings_svc.Keys.ORPHAN_RECLAIM_AFTER_DAYS, value="0", actor=None
    )
    owner = make_user(email="u@test.local", role=UserRole.employee)
    share = _make_share(db, owner.id, state=ShareState.revoked, terminated_ago_days=99)
    f = _make_file(db, share.id, owner.id, state=FileState.clean)
    db.commit()

    res = await reclaim_orphaned_files(None)
    assert res.get("disabled") is True
    db.refresh(f)
    assert f.state == FileState.clean


@pytest.mark.asyncio
async def test_cron_stamps_null_terminated_at_and_defers(make_user, db):
    """A terminal share with no terminated_at (e.g. quarantine-revoked sibling)
    gets stamped now() and is NOT reclaimed this round (full grace from now)."""
    from app.workers.reclaim_orphaned_files import reclaim_orphaned_files

    owner = make_user(email="u@test.local", role=UserRole.employee)
    share = _make_share(db, owner.id, state=ShareState.revoked)  # terminated_at None
    f = _make_file(db, share.id, owner.id, state=FileState.clean)
    db.commit()

    res = await reclaim_orphaned_files(None)
    assert res["reclaimed"] == 0
    db.refresh(share)
    db.refresh(f)
    assert share.terminated_at is not None  # stamped
    assert f.state == FileState.clean  # deferred


# ---- admin reclaim endpoint + orphaned filter -----------------------------


async def _admin_headers(make_user, login_as, email="admin@test.local"):
    make_user(email=email, role=UserRole.admin)
    token, _ = await login_as(email, "TestPassword123!")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_admin_reclaim_endpoint(client, db, make_user, login_as):
    headers = await _admin_headers(make_user, login_as)
    owner = make_user(email="u@test.local", role=UserRole.employee)
    share = _make_share(db, owner.id, state=ShareState.revoked, terminated_ago_days=0)
    f = _make_file(db, share.id, owner.id, state=FileState.clean)
    db.commit()

    r = await client.post(f"/api/admin/files/{f.id}/reclaim", headers=headers)
    assert r.status_code == 204, r.text
    db.refresh(f)
    assert f.state == FileState.deleted


@pytest.mark.asyncio
async def test_admin_reclaim_refuses_active_share_file(client, db, make_user, login_as):
    headers = await _admin_headers(make_user, login_as)
    owner = make_user(email="u@test.local", role=UserRole.employee)
    share = _make_share(db, owner.id, state=ShareState.active)
    f = _make_file(db, share.id, owner.id, state=FileState.clean)
    db.commit()

    r = await client.post(f"/api/admin/files/{f.id}/reclaim", headers=headers)
    assert r.status_code == 409
    assert r.json()["code"] == "NOT_ORPHANED"


@pytest.mark.asyncio
async def test_admin_reclaim_requires_admin(client, db, make_user, login_as):
    make_user(email="emp@test.local", role=UserRole.employee)
    owner = make_user(email="u@test.local", role=UserRole.client)
    share = _make_share(db, owner.id, state=ShareState.revoked, terminated_ago_days=0)
    f = _make_file(db, share.id, owner.id, state=FileState.clean)
    db.commit()
    token, _ = await login_as("emp@test.local", "TestPassword123!")
    r = await client.post(
        f"/api/admin/files/{f.id}/reclaim",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_orphaned_filter_and_flag(client, db, make_user, login_as):
    headers = await _admin_headers(make_user, login_as)
    owner = make_user(email="u@test.local", role=UserRole.employee)
    orphan_share = _make_share(db, owner.id, state=ShareState.revoked, terminated_ago_days=2)
    orphan_f = _make_file(db, orphan_share.id, owner.id, state=FileState.clean, name="orphan.bin")
    active_share = _make_share(db, owner.id, state=ShareState.active)
    _make_file(db, active_share.id, owner.id, state=FileState.clean, name="active.bin")
    db.commit()

    r = await client.get("/api/admin/files?orphaned=true", headers=headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["file_id"] == orphan_f.id
    assert items[0]["is_orphaned"] is True
