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
    """Minimal stand-in for the subset of redis-py used by quota.py."""
    def __init__(self):
        self._store: dict[str, int] = {}

    def exists(self, key):
        return key in self._store

    def set(self, key, value, **_kw):
        self._store[key] = int(value)

    def get(self, key):
        v = self._store.get(key)
        return str(v) if v is not None else None

    def decrby(self, key, amount):
        self._store[key] = max(0, int(self._store.get(key, 0)) - int(amount))

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
