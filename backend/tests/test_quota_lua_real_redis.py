"""The quota enforcement script, run by an actual Redis.

tests-4: `_RESERVE_LUA` decides whether an upload is refused, and no test ever
executed it. The suite's fake Redis re-implements the same logic in Python and
`reserve_bytes` was asserted against that - a mock checked against itself. A
typo in the Lua (an inverted comparison, a missing `tonumber`, the wrong ARGV
index) would have passed every test and disabled quota enforcement in
production, where "fails open" is the documented behaviour on any Redis error.

`_RECONCILE_CAS_LUA` has the same problem with worse consequences: its
skip-on-concurrent-move branch is what stops the hourly reconcile from
clobbering a reservation made while it was reading.

Skipped unless RUN_REDIS_TESTS=1 (the `redis-tests` CI job sets it, alongside a
redis:7-alpine service). Locally: point REDIS_HOST/REDIS_PORT at a throwaway
Redis and set the flag.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import os

import pytest

_SKIP = pytest.mark.skipif(
    os.environ.get("RUN_REDIS_TESTS") != "1",
    reason="needs a real Redis; set RUN_REDIS_TESTS=1 (+ REDIS_HOST/PORT)",
)

pytestmark = _SKIP


@pytest.fixture
def redis_conn():
    from app.redis_client import get_redis

    conn = get_redis()
    conn.ping()
    # Keys used by these tests only; the CI service is exclusive to this job.
    for k in conn.scan_iter("fh:quota:user:9999*"):
        conn.delete(k)
    yield conn
    for k in conn.scan_iter("fh:quota:user:9999*"):
        conn.delete(k)


def _reserve(redis_conn, key: str, size: int, limit: int) -> int:
    """Returns the new total, or -1 when refused.

    The script returns {status, total} since audit #2 - a bare -1 was both the
    refusal sentinel AND a legitimate total for a transiently negative counter,
    which charged the bytes and raised at the same time. Kept as -1 HERE so the
    assertions below still read as "refused"."""
    from app.services.quota import _RESERVE_LUA

    status, total = redis_conn.eval(_RESERVE_LUA, 1, key, size, limit)
    return -1 if int(status) == 1 else int(total)


def _release(redis_conn, key: str, size: int) -> int:
    from app.services.quota import _RELEASE_LUA

    return int(redis_conn.eval(_RELEASE_LUA, 1, key, size))


def test_a_reservation_within_the_limit_returns_the_new_total(redis_conn):
    key = "fh:quota:user:99991"
    assert _reserve(redis_conn, key, 100, 1000) == 100
    assert _reserve(redis_conn, key, 250, 1000) == 350
    assert int(redis_conn.get(key)) == 350


def test_a_reservation_over_the_limit_is_refused_and_charges_nothing(redis_conn):
    """The refusal must not leave the counter incremented, or a user who hits
    their quota once would be charged for the upload that was rejected."""
    key = "fh:quota:user:99992"
    assert _reserve(redis_conn, key, 900, 1000) == 900
    assert _reserve(redis_conn, key, 200, 1000) == -1
    assert int(redis_conn.get(key)) == 900


def test_exactly_at_the_limit_is_allowed(redis_conn):
    """`new_total > limit` refuses, so filling the quota to the byte must pass."""
    key = "fh:quota:user:99993"
    assert _reserve(redis_conn, key, 1000, 1000) == 1000


def test_limit_zero_means_unlimited(redis_conn):
    """NULL quota_bytes is passed as 0. If the script treated 0 as "no bytes
    allowed", every user without an explicit quota could upload nothing."""
    key = "fh:quota:user:99994"
    assert _reserve(redis_conn, key, 10**12, 0) == 10**12


def test_the_first_reservation_seeds_from_zero(redis_conn):
    key = "fh:quota:user:99995"
    redis_conn.delete(key)
    assert _reserve(redis_conn, key, 42, 0) == 42


def test_reserve_bytes_end_to_end_refuses_over_quota(db, make_user, redis_conn):
    """The service, the script and a real Redis together - the path an upload
    actually takes."""
    from app.middleware.errors import AppError
    from app.models.user import UserRole
    from app.services import quota as quota_svc

    user = make_user(email="quota@test.local", role=UserRole.employee)
    user.id = 99996
    user.quota_bytes = 500
    db.commit()
    redis_conn.delete(quota_svc._key(user.id))

    assert quota_svc.reserve_bytes(db, user=user, additional_bytes=400) == 400
    with pytest.raises(AppError) as exc:
        quota_svc.reserve_bytes(db, user=user, additional_bytes=200)
    assert exc.value.code == "QUOTA_EXCEEDED"
    redis_conn.delete(quota_svc._key(user.id))


def test_the_reconcile_script_skips_a_concurrently_moved_counter(redis_conn):
    """`_RECONCILE_CAS_LUA`'s whole job: if the counter changed between the
    worker reading it and writing the recomputed value, the write must be
    abandoned - otherwise the reconcile silently discards a reservation made
    while it was working, and the user is charged for storage they freed or
    granted storage they used."""
    from app.workers.quota_reconcile import _RECONCILE_CAS_LUA

    key = "fh:quota:user:99997"
    redis_conn.set(key, 1000)
    # Expected == actual: the write lands. ARGV[3]='1' means the key existed.
    assert int(redis_conn.eval(_RECONCILE_CAS_LUA, 1, key, "1000", "2000", "1")) == 1
    assert int(redis_conn.get(key)) == 2000
    # Expected != actual (someone reserved in between): skipped.
    assert int(redis_conn.eval(_RECONCILE_CAS_LUA, 1, key, "1000", "5000", "1")) == 0
    assert int(redis_conn.get(key)) == 2000
    redis_conn.delete(key)


def test_the_reconcile_seeds_an_absent_counter_only_when_it_expected_one(redis_conn):
    """ARGV[3]='0' is "there was no key when I read": the write must land only
    if there is still no key, or the reconcile would overwrite a counter that
    was created by a reservation in the meantime."""
    from app.workers.quota_reconcile import _RECONCILE_CAS_LUA

    key = "fh:quota:user:99998"
    redis_conn.delete(key)
    assert int(redis_conn.eval(_RECONCILE_CAS_LUA, 1, key, "", "700", "0")) == 1
    assert int(redis_conn.get(key)) == 700

    redis_conn.set(key, 123)
    assert int(redis_conn.eval(_RECONCILE_CAS_LUA, 1, key, "", "700", "0")) == 0
    assert int(redis_conn.get(key)) == 123
    redis_conn.delete(key)


# --- audit #2: the two edges the sentinel and the floor got wrong ------------


def test_a_negative_counter_does_not_refuse_a_legitimate_reservation(redis_conn):
    """After a Redis flush a release can drive the counter negative. A 1000-byte
    reservation against -1001 produced a new total of -1 - which the old script
    returned as its "over quota" sentinel, so the bytes were charged AND the
    caller was refused, and the retry charged them a second time."""
    key = "fh:quota:user:99998"
    redis_conn.set(key, -1001)
    assert _reserve(redis_conn, key, 1000, 10_000) == 1000
    assert int(redis_conn.get(key)) == 1000, "the drift must be repaired, not credited"
    redis_conn.delete(key)


def test_release_floors_atomically(redis_conn):
    """The floor used to be a second round trip: `DECRBY` then, if negative,
    `SET 0`. A reservation landing between the two was erased - the in-flight
    upload became uncounted and the user could reserve their whole quota again
    on top of it."""
    key = "fh:quota:user:99999"
    redis_conn.set(key, 100)
    assert _release(redis_conn, key, 30) == 70
    assert _release(redis_conn, key, 500) == 0
    assert int(redis_conn.get(key)) == 0
    redis_conn.delete(key)
