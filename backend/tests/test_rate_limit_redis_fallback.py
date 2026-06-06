"""Regression: per-IP rate limit falls back to an in-process limiter when
Redis is down, instead of failing fully open (finding M8)."""
from __future__ import annotations

import pytest

from app.services import rate_limit

# conftest has an autouse fixture that stubs these to always-True; capture
# the REAL implementations at import time (before fixtures run) so we can
# exercise the actual Redis-down fallback path.
_REAL_CHECK_LOGIN = rate_limit.check_login_ip_allowed
_REAL_CHECK_IP = rate_limit.check_ip_allowed


@pytest.fixture(autouse=True)
def _redis_down(monkeypatch):
    def _boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr(rate_limit, "get_redis", _boom)
    # Clear any residue from other tests.
    with rate_limit._local_lock:
        rate_limit._local_windows.clear()
    yield
    with rate_limit._local_lock:
        rate_limit._local_windows.clear()


def test_login_ip_limit_still_bounds_when_redis_down(monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "RATE_LIMIT_LOGIN", 5)
    ip = "203.0.113.50"
    # First 5 allowed, the 6th refused - NOT unlimited.
    results = [_REAL_CHECK_LOGIN(ip) for _ in range(7)]
    assert results[:5] == [True] * 5
    assert results[5] is False
    # A different IP has its own budget.
    assert _REAL_CHECK_LOGIN("198.51.100.7") is True


def test_generic_bucket_limit_still_bounds_when_redis_down():
    ip = "203.0.113.51"
    results = [_REAL_CHECK_IP("forgot", ip, limit=3, window_sec=900) for _ in range(5)]
    assert results[:3] == [True] * 3
    assert results[3] is False
    assert results[4] is False
