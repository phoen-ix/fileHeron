"""Maintenance mode + drain-before-update (v1.34.0)."""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.file import File, FileState
from app.models.share import Share, ShareKind, ShareState
from app.models.user import UserRole
from app.services import maintenance as maintenance_svc
from app.services import transfer_activity as ta


class _Req:
    """Minimal stand-in for a Starlette Request (only `.headers` is read)."""
    def __init__(self, rng: str | None = None):
        self.headers = {"range": rng} if rng else {}


# --------------------------------------------------------------------------- #
# gate
# --------------------------------------------------------------------------- #

def test_gate_passes_when_disabled(db):
    maintenance_svc.refuse_if_maintenance(db, kind="upload")  # no raise


def test_gate_blocks_new_transfer_when_enabled(db):
    maintenance_svc.set_enabled(db, True, actor=None)
    db.commit()
    with pytest.raises(AppError) as ei:
        maintenance_svc.refuse_if_maintenance(db, kind="upload")
    assert ei.value.status_code == 503
    assert ei.value.code == "MAINTENANCE_MODE"


def test_gate_allows_ranged_download_continuation(db):
    maintenance_svc.set_enabled(db, True, actor=None)
    db.commit()
    # a fresh download (no Range / Range from byte 0) is blocked...
    with pytest.raises(AppError):
        maintenance_svc.refuse_if_maintenance(db, request=_Req(), kind="download")
    with pytest.raises(AppError):
        maintenance_svc.refuse_if_maintenance(db, request=_Req("bytes=0-"), kind="download")
    # ...but a continuation (start > 0) is finishing an in-progress download -> allowed
    maintenance_svc.refuse_if_maintenance(db, request=_Req("bytes=500-"), kind="download")


def test_custom_message_surfaced(db):
    maintenance_svc.set_enabled(db, True, actor=None, message="back at 5pm")
    db.commit()
    with pytest.raises(AppError) as ei:
        maintenance_svc.refuse_if_maintenance(db, kind="upload")
    assert ei.value.message == "back at 5pm"


# --------------------------------------------------------------------------- #
# transfer activity
# --------------------------------------------------------------------------- #

class _FakeZSet:
    def __init__(self):
        self.d: dict[str, float] = {}

    def zadd(self, key, mapping):
        self.d.update(mapping)

    def zrem(self, key, member):
        self.d.pop(member, None)

    def zcard(self, key):
        return len(self.d)

    def zremrangebyscore(self, key, lo, hi):
        for m in [m for m, s in self.d.items() if lo <= s <= hi]:
            del self.d[m]


def test_download_counter_math(monkeypatch):
    fake = _FakeZSet()
    monkeypatch.setattr(ta, "get_redis", lambda: fake)
    t = {"now": 1000.0}
    monkeypatch.setattr(ta, "_now", lambda: t["now"])

    assert ta.active_downloads() == 0
    a = ta.download_started()
    ta.download_started()  # second in-flight download (leaks, no finish)
    assert ta.active_downloads() == 2
    ta.download_finished(a)
    assert ta.active_downloads() == 1
    # b leaks (no finish); advance past the prune age -> self-heals to 0
    t["now"] = 1000.0 + ta.MAX_DOWNLOAD_AGE_SEC + 1
    assert ta.active_downloads() == 0


def test_active_downloads_failopen(monkeypatch):
    def _boom():
        raise RuntimeError("redis down")
    monkeypatch.setattr(ta, "get_redis", _boom)
    assert ta.active_downloads() == 0  # never blocks an update on a redis outage


def test_active_uploads_counts_uploading(db, make_user):
    owner = make_user(email="up@test.local", role=UserRole.employee)
    share = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(share)
    db.commit()
    db.add(File(share_id=share.id, original_filename="a", size_bytes=1,
                state=FileState.uploading, uploaded_by_id=owner.id))
    db.add(File(share_id=share.id, original_filename="b", size_bytes=1,
                state=FileState.clean, uploaded_by_id=owner.id))
    db.commit()
    assert ta.active_uploads(db) == 1


# --------------------------------------------------------------------------- #
# pending update record + drain worker
# --------------------------------------------------------------------------- #

def test_apply_pending_update_hands_off_with_the_gate_still_shut(db, monkeypatch):
    """Maintenance used to be lifted here, at job-WRITE time, which re-opened
    uploads and downloads for the whole image-pull window - the one stretch
    where a new transfer is most likely to be cut off by the restart it just
    raced. It now stays on until the new container boots (audit 2026-07-30,
    flow-maintenance-5)."""
    from app.services import release_apply

    calls = {}
    def _fake_apply(*, action, target_tag):
        calls["action"] = action
        calls["target_tag"] = target_tag
        return {"job_id": "job-1", "action": action, "target_tag": target_tag}
    monkeypatch.setattr(release_apply, "apply", _fake_apply)

    maintenance_svc.set_enabled(db, True, actor=None)
    maintenance_svc.set_pending_update(db, {"target_tag": "v9.9.9", "deadline_iso": "x"}, actor=None)
    db.commit()

    result = maintenance_svc.apply_pending_update(db, reason="drain")
    assert result["job_id"] == "job-1"
    assert calls == {"action": "update", "target_tag": "v9.9.9"}
    assert maintenance_svc.is_enabled(db) is True, "the gate re-opened mid-pull"
    assert maintenance_svc.get_pending_update(db) is None
    assert maintenance_svc.get_handoff_at(db) is not None

    # ...and the new container's boot is what lifts it.
    assert maintenance_svc.clear_maintenance_after_update(db) is True
    assert maintenance_svc.is_enabled(db) is False


@pytest.mark.asyncio
async def test_drain_worker_noop_without_pending(db):
    from app.workers.drain_pending_update import drain_pending_update

    out = await drain_pending_update(None)
    assert out["pending"] is False


@pytest.mark.asyncio
async def test_drain_worker_waits_when_active(db, monkeypatch):
    from app.workers import drain_pending_update as mod

    maintenance_svc.set_enabled(db, True, actor=None)
    maintenance_svc.set_pending_update(
        db, {"target_tag": "v9.9.9", "deadline_iso": "2999-01-01T00:00:00"}, actor=None
    )
    db.commit()
    monkeypatch.setattr(mod.ta, "active_uploads", lambda _db: 1)
    monkeypatch.setattr(mod.ta, "active_downloads", lambda: 0)

    out = await mod.drain_pending_update(None)
    assert out["triggered"] is False
    assert maintenance_svc.is_enabled(db) is True  # still waiting


@pytest.mark.asyncio
async def test_drain_worker_fires_when_drained(db, monkeypatch):
    from app.services import release_apply
    from app.workers import drain_pending_update as mod

    monkeypatch.setattr(
        release_apply, "apply",
        lambda *, action, target_tag: {"job_id": "j", "action": action, "target_tag": target_tag},
    )
    maintenance_svc.set_enabled(db, True, actor=None)
    maintenance_svc.set_pending_update(
        db, {"target_tag": "v9.9.9", "deadline_iso": "2999-01-01T00:00:00"}, actor=None
    )
    db.commit()
    monkeypatch.setattr(mod.ta, "active_uploads", lambda _db: 0)
    monkeypatch.setattr(mod.ta, "active_downloads", lambda: 0)

    out = await mod.drain_pending_update(None)
    assert out["triggered"] is True
    assert out["reason"] == "drain"
    # Still shut: the updated container lifts it on boot, not the hand-off.
    assert maintenance_svc.is_enabled(db) is True
    assert maintenance_svc.get_pending_update(db) is None


@pytest.mark.asyncio
async def test_drain_worker_fires_past_deadline(db, monkeypatch):
    from app.services import release_apply
    from app.workers import drain_pending_update as mod

    monkeypatch.setattr(
        release_apply, "apply",
        lambda *, action, target_tag: {"job_id": "j", "action": action, "target_tag": target_tag},
    )
    maintenance_svc.set_enabled(db, True, actor=None)
    maintenance_svc.set_pending_update(
        db, {"target_tag": "v9.9.9", "deadline_iso": "2000-01-01T00:00:00"}, actor=None
    )
    db.commit()
    monkeypatch.setattr(mod.ta, "active_uploads", lambda _db: 3)  # still busy
    monkeypatch.setattr(mod.ta, "active_downloads", lambda: 2)

    out = await mod.drain_pending_update(None)
    assert out["triggered"] is True
    assert out["reason"] == "deadline"


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_update_postpone_sets_maintenance_without_applying(
    client, make_user, login_as, db, monkeypatch
):
    from app.services import release_apply

    # apply() must NOT be called on a postpone
    def _fail(*a, **k):
        raise AssertionError("release_apply.apply should not run on postpone")
    monkeypatch.setattr(release_apply, "apply", _fail)

    make_user(email="boss@test.local", password="TestPassword123!", role=UserRole.admin)
    token, _ = await login_as("boss@test.local", "TestPassword123!")
    resp = await client.post(
        "/api/admin/system/update",
        json={"password": "TestPassword123!", "target_tag": "v9.9.9", "postpone": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["postponed"] is True
    assert maintenance_svc.is_enabled(db) is True
    pending = maintenance_svc.get_pending_update(db)
    assert pending["target_tag"] == "v9.9.9"


@pytest.mark.asyncio
async def test_transfer_activity_requires_admin(client, make_user, login_as):
    make_user(email="emp@test.local", password="TestPassword123!", role=UserRole.employee)
    token, _ = await login_as("emp@test.local", "TestPassword123!")
    resp = await client.get(
        "/api/admin/system/transfer-activity",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cancel_pending_update(client, make_user, login_as, db):
    make_user(email="boss2@test.local", password="TestPassword123!", role=UserRole.admin)
    token, _ = await login_as("boss2@test.local", "TestPassword123!")
    maintenance_svc.set_enabled(db, True, actor=None)
    maintenance_svc.set_pending_update(db, {"target_tag": "v9.9.9", "deadline_iso": "x"}, actor=None)
    db.commit()

    resp = await client.post(
        "/api/admin/system/update/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is True
    assert maintenance_svc.is_enabled(db) is False
    assert maintenance_svc.get_pending_update(db) is None


@pytest.mark.asyncio
async def test_config_public_exposes_maintenance(client, db):
    maintenance_svc.set_enabled(db, True, actor=None, message="brb")
    db.commit()
    resp = await client.get("/api/config-public")
    assert resp.status_code == 200
    body = resp.json()
    assert body["maintenance"] == {"enabled": True, "message": "brb"}
