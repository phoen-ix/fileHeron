"""ops_check's ClamAV check retries before it calls clamd unhealthy.

clamd stops answering for several seconds while it activates a freshly
downloaded signature database. freshclam does that once a day at a time
anchored to the clamav container's start, and on 2026-10-01 and -02 the swap
landed on the hourly check at 13:04:01: one ping timed out, and every admin was
mailed "av_unhealthy" about a daemon that never went down. A miss is now
retried; only a failure that outlasts the retries alerts.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.models.user import UserRole
from app.services import av_scan
from app.workers import ops_check as ops


@pytest.fixture
def av_on(monkeypatch):
    monkeypatch.setattr(settings, "AV_SKIP", False)


@pytest.fixture
def sleeps(monkeypatch):
    """Record the waits between attempts instead of sleeping through them."""
    waited: list[float] = []

    async def _sleep(seconds):
        waited.append(seconds)

    monkeypatch.setattr(ops.asyncio, "sleep", _sleep)
    return waited


def _pings(monkeypatch, answers):
    calls: list[int] = []
    seq = iter(answers)

    def _ping():
        calls.append(1)
        return next(seq)

    monkeypatch.setattr(av_scan, "ping", _ping)
    return calls


@pytest.mark.asyncio
async def test_a_reload_blip_is_not_an_outage(av_on, sleeps, monkeypatch):
    calls = _pings(monkeypatch, [False, True])
    assert await ops._check_av(None) is None
    assert len(calls) == 2
    assert sleeps == [ops._AV_PING_RETRY_DELAY_SEC], "it waits before trying again"


@pytest.mark.asyncio
async def test_an_outage_that_outlasts_the_retries_is_reported(av_on, sleeps, monkeypatch):
    calls = _pings(monkeypatch, [False] * ops._AV_PING_ATTEMPTS)
    err = await ops._check_av(None)
    assert err == "ClamAV ping returned false 3 times over 20 s"
    assert len(calls) == ops._AV_PING_ATTEMPTS
    assert sleeps == [ops._AV_PING_RETRY_DELAY_SEC] * (ops._AV_PING_ATTEMPTS - 1)


@pytest.mark.asyncio
async def test_a_healthy_clamd_is_asked_once(av_on, sleeps, monkeypatch):
    calls = _pings(monkeypatch, [True])
    assert await ops._check_av(None) is None
    assert (len(calls), sleeps) == (1, [])


@pytest.mark.asyncio
async def test_a_raising_ping_is_reported(av_on, sleeps, monkeypatch):
    def _boom():
        raise RuntimeError("socket gone")

    monkeypatch.setattr(av_scan, "ping", _boom)
    assert await ops._check_av(None) == "ClamAV check raised: socket gone"


@pytest.mark.asyncio
async def test_av_skip_never_pings(sleeps, monkeypatch):
    monkeypatch.setattr(settings, "AV_SKIP", True)
    calls = _pings(monkeypatch, [])
    assert await ops._check_av(None) is None
    assert calls == []


@pytest.fixture
def sent(monkeypatch):
    out: list = []
    monkeypatch.setattr(
        ops, "dispatch", lambda db, *, user, category, payload, **kw: out.append(payload)
    )
    monkeypatch.setattr(ops, "_check_redis", lambda: None)
    monkeypatch.setattr(ops, "_check_smtp", lambda _db: None)
    monkeypatch.setattr(ops, "_check_failing_crons", lambda _db: None)
    return out


@pytest.mark.asyncio
async def test_the_hourly_run_stays_quiet_through_a_reload(db, make_user, av_on, sleeps, sent, monkeypatch):
    make_user(email="ops-admin@test.local", role=UserRole.admin)
    db.commit()
    _pings(monkeypatch, [False, True])
    result = await ops.ops_check({})
    assert (result["av"], result["dispatched"], sent) == ("ok", 0, [])


@pytest.mark.asyncio
async def test_the_hourly_run_alerts_on_a_real_outage(db, make_user, av_on, sleeps, sent, monkeypatch):
    make_user(email="ops-admin@test.local", role=UserRole.admin)
    db.commit()
    _pings(monkeypatch, [False] * ops._AV_PING_ATTEMPTS)
    result = await ops.ops_check({})
    assert result["dispatched"] == 1
    assert [p["reason"] for p in sent] == ["av_unhealthy"]
    assert sent[0]["detail"] == result["av"]
