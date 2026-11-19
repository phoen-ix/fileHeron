"""A rate-limit window can never lose its TTL (audit 2026-11-19).

The counter used to get its expiry from a separate EXPIRE sent only when INCR
returned 1. If that one call failed - a 2 s socket timeout, Redis restarting
between the two commands - the key never expired, and the IP or user was
refused for good once over the limit. `EXPIRE ... NX` now follows every INCR:
a key without a TTL is repaired by the next hit, and an existing TTL is never
extended (the window stays fixed).
"""
from __future__ import annotations

from app.services import rate_limit

# Captured at import, before conftest's autouse fixture stubs them per test.
_REAL_CHECK_IP = rate_limit.check_ip_allowed
_REAL_CHECK_USER = rate_limit.check_user_allowed


class _FakeRedis:
    """INCR and EXPIRE with real NX semantics, in a dict."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key, ttl, nx=False):
        if nx and key in self.ttls:
            return False
        self.ttls[key] = ttl
        return True


def test_a_counter_that_lost_its_ttl_gets_one_back(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "get_redis", lambda: fake)
    key = rate_limit._bucket_key("login", "203.0.113.9")
    fake.counts[key] = 50  # over any limit, and no TTL: the old failure mode

    assert _REAL_CHECK_IP("login", "203.0.113.9", limit=10, window_sec=900) is False
    assert fake.ttls[key] == 900, "the next hit must give the key its TTL back"


def test_the_window_is_fixed_not_sliding(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "get_redis", lambda: fake)
    assert _REAL_CHECK_IP("forgot", "203.0.113.9", limit=3, window_sec=900) is True
    key = rate_limit._bucket_key("forgot", "203.0.113.9")
    fake.ttls[key] = 12  # the window is nearly over
    assert _REAL_CHECK_IP("forgot", "203.0.113.9", limit=3, window_sec=900) is True
    assert fake.ttls[key] == 12, "a hit must never extend the window"


def test_the_per_user_window_uses_the_same_primitive(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "get_redis", lambda: fake)
    key = rate_limit._user_key("step_up", 7)
    fake.counts[key] = 5
    assert _REAL_CHECK_USER("step_up", 7, limit=5, window_sec=900) is False
    assert fake.ttls[key] == 900
