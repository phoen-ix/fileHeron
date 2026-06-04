"""Per-user quota reservation, release, and Redis fail-open behavior.

Redis is mocked with an in-memory dict that implements the subset of
the redis client surface that `services/quota.py` and the Lua reserve
script touch. Pure unit coverage — no fakeredis dependency.
"""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.user import UserRole
from app.services import quota as quota_svc


class _FakeRedis:
    """Minimal stand-in for the subset of redis-py used by quota.py.

    Models real redis semantics that matter here: SET clears/sets TTL, SET NX
    is a no-op on an existing key, DECRBY returns the new value and may go
    negative (the floor lives in quota.release_bytes, not the server)."""
    def __init__(self):
        self._store: dict[str, int] = {}
        self._ttls: dict[str, int] = {}

    def exists(self, key):
        return key in self._store

    def set(self, key, value, ex=None, nx=False, **_kw):
        if nx and key in self._store:
            return
        self._store[key] = int(value)
        self._ttls[key] = ex if ex is not None else -1

    def ttl(self, key):
        if key not in self._store:
            return -2
        return self._ttls.get(key, -1)

    def get(self, key):
        v = self._store.get(key)
        return str(v) if v is not None else None

    def decrby(self, key, amount):
        self._store[key] = int(self._store.get(key, 0)) - int(amount)
        return self._store[key]

    def eval(self, _script, _num_keys, key, size, limit):
        size, limit = int(size), int(limit)
        current = int(self._store.get(key, 0))
        new_total = current + size
        if limit > 0 and new_total > limit:
            return -1
        self._store[key] = new_total
        return new_total


@pytest.fixture
def fake_redis(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(quota_svc, "get_redis", lambda: redis)
    return redis


def test_reserve_under_quota_returns_new_total(make_user, db, fake_redis):
    user = make_user(email="alice@test.local", role=UserRole.client)
    user.quota_bytes = 1_000_000
    db.commit()

    new_total = quota_svc.reserve_bytes(db, user=user, additional_bytes=500_000)
    assert new_total == 500_000

    new_total = quota_svc.reserve_bytes(db, user=user, additional_bytes=300_000)
    assert new_total == 800_000


def test_reserve_over_quota_raises(make_user, db, fake_redis):
    user = make_user(email="alice@test.local", role=UserRole.client)
    user.quota_bytes = 1_000_000
    db.commit()

    quota_svc.reserve_bytes(db, user=user, additional_bytes=900_000)
    with pytest.raises(AppError) as exc:
        quota_svc.reserve_bytes(db, user=user, additional_bytes=200_000)
    assert exc.value.code == "QUOTA_EXCEEDED"
    assert exc.value.status_code == 413


def test_unlimited_quota_never_rejects(make_user, db, fake_redis):
    user = make_user(email="alice@test.local", role=UserRole.client)
    user.quota_bytes = None  # unlimited
    db.commit()

    # Two huge reservations both succeed.
    quota_svc.reserve_bytes(db, user=user, additional_bytes=10_000_000_000)
    new_total = quota_svc.reserve_bytes(db, user=user, additional_bytes=10_000_000_000)
    assert new_total == 20_000_000_000


def test_release_decrements_counter(make_user, db, fake_redis):
    user = make_user(email="alice@test.local", role=UserRole.client)
    user.quota_bytes = 1_000_000
    db.commit()

    quota_svc.reserve_bytes(db, user=user, additional_bytes=500_000)
    quota_svc.release_bytes(user_id=user.id, bytes_to_free=200_000)
    assert quota_svc.used_bytes(user_id=user.id) == 300_000


def test_release_clamps_at_zero(make_user, db, fake_redis):
    user = make_user(email="alice@test.local", role=UserRole.client)
    db.commit()

    # Releasing more than reserved doesn't go negative.
    quota_svc.release_bytes(user_id=user.id, bytes_to_free=1_000_000)
    assert quota_svc.used_bytes(user_id=user.id) == 0


def test_used_bytes_floors_negative_counter(make_user, db, fake_redis):
    user = make_user(email="alice@test.local", role=UserRole.client)
    db.commit()
    # A drifted negative counter (release-without-reserve) must read as 0 so it
    # can't loosen enforcement.
    fake_redis._store[quota_svc._key(user.id)] = -150
    assert quota_svc.used_bytes(user_id=user.id) == 0


def _seed_file(db, *, owner_id, share_id, fid, size, state):
    from app.models.file import File
    db.add(File(
        id=fid, share_id=share_id, uploaded_by_id=owner_id,
        original_filename=fid, mime_type="application/octet-stream",
        size_bytes=size, storage_path=f"/{fid}", state=state,
    ))


def _make_share(db, owner_id):
    from datetime import datetime, timedelta, timezone

    from app.models.share import Share, ShareKind, ShareState
    s = Share(
        created_by_id=owner_id, kind=ShareKind.outbound, subject=None, message=None,
        expires_at=(datetime.now(tz=timezone.utc) + timedelta(hours=1)).replace(tzinfo=None),
        state=ShareState.active,
    )
    db.add(s)
    db.flush()
    return s.id


def test_initialize_from_db_sets_no_ttl(make_user, db, fake_redis):
    from app.models.file import FileState

    user = make_user(email="alice@test.local", role=UserRole.client)
    sid = _make_share(db, user.id)
    _seed_file(db, owner_id=user.id, share_id=sid, fid="q-ttl-1", size=4096, state=FileState.clean)
    db.commit()
    used = quota_svc._initialize_from_db(db, user.id)
    assert used == 4096
    # No expiry — the counter must not silently lapse between reconcile runs.
    assert fake_redis.ttl(quota_svc._key(user.id)) == -1


def test_storage_used_bytes_sums_from_db(make_user, db, fake_redis):
    from app.models.file import FileState

    user = make_user(email="alice@test.local", role=UserRole.client)
    sid = _make_share(db, user.id)
    _seed_file(db, owner_id=user.id, share_id=sid, fid="q-a", size=1000, state=FileState.clean)
    _seed_file(db, owner_id=user.id, share_id=sid, fid="q-b", size=500, state=FileState.ready_unscanned)
    _seed_file(db, owner_id=user.id, share_id=sid, fid="q-c", size=9999, state=FileState.deleted)
    db.commit()
    # Redis counter deliberately wrong — DB sum is authoritative, excludes deleted.
    fake_redis._store[quota_svc._key(user.id)] = 7
    assert quota_svc.storage_used_bytes(db, user_id=user.id) == 1500
    assert quota_svc.storage_used_bytes_bulk(db, [user.id, 999]) == {user.id: 1500, 999: 0}


def test_negative_size_rejected(make_user, db, fake_redis):
    user = make_user(email="alice@test.local", role=UserRole.client)
    db.commit()

    with pytest.raises(AppError) as exc:
        quota_svc.reserve_bytes(db, user=user, additional_bytes=-1)
    assert exc.value.code == "INVALID_SIZE"


def test_redis_unreachable_fails_open(make_user, db, monkeypatch):
    user = make_user(email="alice@test.local", role=UserRole.client)
    user.quota_bytes = 1_000_000
    db.commit()

    def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(quota_svc, "get_redis", boom)

    # CLAUDE.md: quota is a fairness control, not a hard cap. On Redis
    # outage, the upload is allowed through.
    result = quota_svc.reserve_bytes(db, user=user, additional_bytes=42)
    assert result == 42


def test_used_bytes_returns_zero_when_unset(fake_redis):
    assert quota_svc.used_bytes(user_id=999) == 0
