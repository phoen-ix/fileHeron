"""A notification fan-out publishes its live (SSE) events over one connection.

`dispatch` registered one after-commit `publish_sync` per in-app recipient, and
`publish_sync` opens a Redis client per call - from a sync route, inside its own
`asyncio.run`. The emails of the same fan-out were batched (dos-15); the bell's
pushes were not. They now ride the same per-commit batch, published by
`sse.publish_many` over one pipeline. These count connections, because that is
what was wrong, and pin the batch semantics the email path already has.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.models.notification import NotificationCategory
from app.models.user import UserRole
from app.services import notification as notif_svc
from app.services import sse as _sse

# Captured at import, before conftest's autouse stub replaces them.
_REAL = {name: getattr(_sse, name) for name in ("publish_many", "publish_many_sync")}


class _FakeRedis:
    connections = 0
    published: list[tuple[str, dict]] = []

    def __init__(self):
        type(self).connections += 1

    def pipeline(self, transaction=True):
        assert transaction is False
        return _FakePipe()

    async def aclose(self):
        return None


class _FakePipe:
    def __init__(self):
        self.queued: list[tuple[str, dict]] = []

    def publish(self, channel, payload):
        self.queued.append((channel, json.loads(payload)))
        return self

    async def execute(self):
        _FakeRedis.published.extend(self.queued)
        return [0] * len(self.queued)


@pytest.fixture
def conn_counter(monkeypatch):
    _FakeRedis.connections = 0
    _FakeRedis.published = []
    for name, fn in _REAL.items():
        monkeypatch.setattr(_sse, name, fn)
    monkeypatch.setattr(_sse, "_redis", lambda: _FakeRedis())
    return _FakeRedis


def _dispatch_to(db, users):
    for u in users:
        notif_svc.dispatch(
            db,
            user=u,
            category=NotificationCategory.share_created,
            payload={"sender_name": "Someone", "subject": "A share", "share_id": "s-1"},
        )


def test_ten_recipients_cost_one_connection(db, make_user, conn_counter):
    users = [make_user(email=f"r{i}@test.local", role=UserRole.employee) for i in range(10)]
    db.commit()
    _dispatch_to(db, users)
    assert conn_counter.connections == 0, "nothing may be published before the commit"
    db.commit()
    assert conn_counter.connections == 1
    channels = sorted(ch for ch, _ev in conn_counter.published)
    assert channels == sorted(f"fh:sse:{u.id}" for u in users)
    assert all(ev["event"] == "notification" for _ch, ev in conn_counter.published)


def test_a_rollback_publishes_nothing(db, make_user, conn_counter):
    users = [make_user(email=f"u{i}@test.local", role=UserRole.employee) for i in range(3)]
    db.commit()
    _dispatch_to(db, users)
    db.rollback()
    db.commit()
    assert conn_counter.connections == 0 and conn_counter.published == []


def test_a_rolled_back_batch_is_not_adopted_by_the_next_dispatch(db, make_user, conn_counter):
    doomed = [make_user(email=f"v{i}@test.local", role=UserRole.employee) for i in range(3)]
    survivor = make_user(email="survivor@test.local", role=UserRole.employee)
    db.commit()
    _dispatch_to(db, doomed)
    db.rollback()
    _dispatch_to(db, [survivor])
    db.commit()
    assert [ch for ch, _ev in conn_counter.published] == [f"fh:sse:{survivor.id}"]


def test_a_second_commit_starts_a_fresh_batch(db, make_user, conn_counter):
    a = make_user(email="a1@test.local", role=UserRole.employee)
    b = make_user(email="b1@test.local", role=UserRole.employee)
    db.commit()
    _dispatch_to(db, [a])
    db.commit()
    _dispatch_to(db, [b])
    db.commit()
    assert conn_counter.connections == 2
    assert [ch for ch, _ev in conn_counter.published] == [f"fh:sse:{a.id}", f"fh:sse:{b.id}"]


@pytest.mark.asyncio
async def test_inside_a_running_loop_the_batch_is_still_published(db, make_user, conn_counter):
    """ARQ crons call dispatch synchronously from an `async def`: the batch is
    then a tracked task on the running loop, not an asyncio.run."""
    users = [make_user(email=f"w{i}@test.local", role=UserRole.employee) for i in range(3)]
    db.commit()
    _dispatch_to(db, users)
    db.commit()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert conn_counter.connections == 1
    assert len(conn_counter.published) == 3


def test_a_redis_failure_is_logged_not_raised(db, make_user, monkeypatch, caplog):
    for name, fn in _REAL.items():
        monkeypatch.setattr(_sse, name, fn)

    def _down():
        raise ConnectionError("redis is down")

    monkeypatch.setattr(_sse, "_redis", _down)
    u = make_user(email="x@test.local", role=UserRole.employee)
    db.commit()
    _dispatch_to(db, [u])
    db.commit()  # must not raise
    assert "SSE batch publish failed" in caplog.text
