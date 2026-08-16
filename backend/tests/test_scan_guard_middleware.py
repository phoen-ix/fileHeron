"""Scan-guard wiring: prove the block by forcing a denial, and prove the refusals.

Two harness facts make these tests possible, and both are easy to trip over:

1. conftest's autouse `_disable_ip_rate_limit` stubs `check_ip_allowed` to return
   True for the whole suite, so no other test can accidentally block. These tests
   monkeypatch it themselves.
2. `httpx.ASGITransport` defaults `scope["client"]` to `127.0.0.1`, which the
   non-global rule refuses to block. That is precisely what keeps the other ~250
   test files green without touching them - and it means a blocking test must
   pass an explicit public `client=`.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.models.ip_block import IpBlock
from app.models.user import UserRole
from app.services import scan_guard as sg

# Bound at import time, which happens during collection - BEFORE conftest's
# autouse `_disable_ip_rate_limit` replaces the module attribute. This is the
# real limiter, not a re-implementation of it: a test that re-derived the
# `count <= limit` arithmetic could not disagree with the code it checks, which
# is the anti-pattern this suite has been bitten by repeatedly.
from app.services.rate_limit import check_ip_allowed as _real_check_ip_allowed

SCANNER = "45.148.10.67"
PW = "Pass12345678!"


@pytest.fixture(autouse=True)
def _fresh_cache():
    sg._reset_cache()
    yield
    sg._reset_cache()


@pytest_asyncio.fixture
async def scanner_client(app_with_db):
    """A client that looks like it comes from a real, globally-routable address."""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_db, client=(SCANNER, 51234)),
        base_url="http://test",
    ) as ac:
        yield ac


def _enable(db, **over):
    """Turn the guard on with `enabled` + `signal_probe_path`, plus whatever
    else the caller names.

    It used to write ONLY those two keys and silently ignore every other kwarg,
    which is how `test_an_authenticated_user_is_never_blocked` came to pass
    vacuously: it asked for `signal_api_404`, never got it, drove an `/api/`
    path that the probe_path branch skips, and so `classify` returned None
    whether or not the authenticated short-circuit existed at all."""
    from app.services import settings as settings_svc

    values = {"enabled": True, "signal_probe_path": True}
    values.update(over)
    keys = settings_svc.Keys
    for field, value in values.items():
        key = getattr(keys, f"SCAN_GUARD_{field.upper()}")
        stored = (
            ("true" if value else "false") if isinstance(value, bool) else str(value)
        )
        settings_svc.set_value(db, key=key, value=stored, actor=None)
    db.commit()
    sg._reset_cache()


async def _settled():
    """Counting is synchronous, so there is nothing to wait for.

    Kept as an explicit marker at the points where a test depends on the
    offence having been recorded by the time the next line runs. If counting is
    ever moved off the response path, this is the one place that has to learn
    how to wait - rather than every assertion in the file becoming flaky."""
    return None


@pytest.mark.asyncio
async def test_a_blocked_response_is_indistinguishable_from_a_real_404(
    db, app_with_db, scanner_client
):
    """The whole point of answering 404. Anything that differs is an oracle: a
    scanner learns which of its proxies are burned and can binary-search the
    threshold, and someone spoofed into a block gets free confirmation."""
    _enable(db)
    real = await scanner_client.get("/api/does-not-exist")
    assert real.status_code == 404

    sg.apply_block(db, subject=SCANNER, reason="probe_path", snap=sg.snapshot())
    db.commit()
    sg._reset_cache()

    blocked = await scanner_client.get("/api/does-not-exist")
    assert blocked.status_code == 404
    assert blocked.headers["content-type"] == real.headers["content-type"]
    assert blocked.json()["code"] == real.json()["code"] == "NOT_FOUND"
    assert set(blocked.json()) == set(real.json())
    for header in (
        "x-request-id",
        "x-content-type-options",
        "x-frame-options",
        "referrer-policy",
        "content-security-policy",
    ):
        assert header in blocked.headers, header
        assert blocked.headers[header] == real.headers[header] or header == "x-request-id"
    # And nothing announces the guard's existence.
    for leak in ("x-blocked-by", "retry-after", "x-scan-guard"):
        assert leak not in blocked.headers


@pytest.mark.asyncio
async def test_a_block_hides_an_otherwise_working_route(db, scanner_client):
    _enable(db)
    ok = await scanner_client.get("/api/health")
    assert ok.status_code == 200

    sg.apply_block(db, subject=SCANNER, reason="probe_path", snap=sg.snapshot())
    db.commit()
    sg._reset_cache()
    assert (await scanner_client.get("/api/health")).status_code == 404


@pytest.mark.asyncio
async def test_the_guard_does_nothing_while_disabled(db, scanner_client):
    """Ships OFF, so an upgrade is behaviour-neutral: a row can even exist and
    still nothing is refused until an admin opts in."""
    sg.apply_block(db, subject=SCANNER, reason="probe_path", snap=sg._defaults())
    db.commit()
    sg._reset_cache()
    assert (await scanner_client.get("/api/health")).status_code == 200


@pytest.mark.asyncio
async def test_an_allowlisted_source_is_never_refused(db, scanner_client):
    from app.services import settings as settings_svc

    _enable(db)
    settings_svc.set_value(
        db, key=settings_svc.Keys.SCAN_GUARD_ALLOWLIST,
        value=f"{SCANNER}/32", actor=None,
    )
    # Commit BEFORE anything calls into the guard. The cache refresh opens its
    # own SessionLocal, and under the test harness's StaticPool every session
    # shares one connection - so closing that refresh session rolls back writes
    # this one has not committed yet. Production sessions are independent
    # connections and do not interact this way.
    db.commit()
    sg.apply_block(db, subject=SCANNER, reason="probe_path", snap=sg.snapshot())
    db.commit()
    sg._reset_cache()
    assert (await scanner_client.get("/api/health")).status_code == 200


@pytest.mark.asyncio
async def test_a_blocked_request_writes_no_error_log_row_and_enqueues_nothing(
    db, scanner_client, monkeypatch
):
    """The feedback loop, closed structurally rather than suppressed. A blocked
    scanner keeps hammering; if each refusal re-entered the error path it would
    manufacture unbounded error_log rows and ARQ jobs - a self-DoS caused by the
    defence. Blocking must QUIET the log."""
    from app.services import job_queue

    _enable(db)
    sg.apply_block(db, subject=SCANNER, reason="probe_path", snap=sg.snapshot())
    db.commit()
    sg._reset_cache()

    enqueued: list = []
    monkeypatch.setattr(job_queue, "enqueue", lambda *a, **kw: enqueued.append(a))

    for _ in range(5):
        assert (await scanner_client.get("/.env")).status_code == 404
    assert enqueued == [], "a blocked request must not reach the error-capture path"


@pytest.mark.asyncio
async def test_a_bait_path_blocks_the_source(db, scanner_client, monkeypatch):
    """Detection wiring, forced: deny the counter so the very first offending
    request is the one that crosses the threshold."""
    from app.services import rate_limit

    _enable(db)
    monkeypatch.setattr(
        rate_limit, "check_ip_allowed",
        lambda bucket, ip, limit=0, window_sec=0: bucket != "scanguard",
    )
    assert (await scanner_client.get("/.env")).status_code == 404
    await _settled()
    row = db.query(IpBlock).filter(IpBlock.subject == SCANNER).one_or_none()
    assert row is not None
    assert row.reason == "probe_path"
    assert row.network == "45.148.10.0/24"


@pytest.mark.asyncio
async def test_loopback_is_never_blocked_however_hard_it_probes(
    db, client, monkeypatch
):
    """`client` is the default fixture, whose scope client is 127.0.0.1 - which is
    also what the frontend nginx, tusd, the updater and the compose healthcheck
    look like when FORWARDED_ALLOW_IPS is pinned as this repo's docs advise."""
    from app.services import rate_limit

    _enable(db)
    monkeypatch.setattr(
        rate_limit, "check_ip_allowed",
        lambda bucket, ip, limit=0, window_sec=0: bucket != "scanguard",
    )
    for _ in range(5):
        await client.get("/.env")
    assert db.query(IpBlock).count() == 0


@pytest.mark.asyncio
async def test_an_authenticated_user_is_never_blocked(
    db, app_with_db, make_user, login_as, monkeypatch
):
    """What stops the product blocking its own users for using it.

    The positive control at the end is the whole point of this test's shape:
    without it, the test passed while proving nothing. It enabled only
    `signal_probe_path`, then drove `/api/shares/nope` - an `/api/` path, which
    the probe_path branch skips by construction - so `classify` returned None
    regardless, and deleting the authenticated short-circuit left it green.
    """
    from app.services import rate_limit

    # signal_api_404, so the traffic below is genuinely classifiable.
    _enable(db, signal_api_404=True, min_distinct_paths=1)
    make_user(email="u@test.local", role=UserRole.employee, password=PW)
    monkeypatch.setattr(
        rate_limit, "check_ip_allowed",
        lambda bucket, ip, limit=0, window_sec=0: bucket.startswith("scanblock"),
    )
    monkeypatch.setattr(sg, "_distinct_paths_seen", lambda ip, path, window: 99)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_db, client=(SCANNER, 51234)),
        base_url="http://test",
    ) as ac:
        login = await ac.post(
            "/api/auth/login", json={"email": "u@test.local", "password": PW}
        )
        token = login.json()["access_token"]
        # A route that EXISTS and 404s (no such share). That matters: the
        # `authenticated` mark is set by the auth dependency, which only runs
        # for a request that matched a route - see the test below.
        for _ in range(5):
            resp = await ac.get(
                "/api/shares/nope", headers={"Authorization": f"Bearer {token}"}
            )
            assert resp.status_code == 404, resp.text
        await _settled()
        assert db.query(IpBlock).count() == 0, "an authenticated user was blocked"

        # Positive control: an anonymous /api/ 404 under the SAME settings must
        # block, so this test can never again pass because the traffic it drove
        # was unclassifiable.
        assert (await ac.get("/api/definitely-not-a-route")).status_code == 404
        await _settled()

    assert db.query(IpBlock).filter(IpBlock.subject == SCANNER).count() == 1


@pytest.mark.asyncio
async def test_a_404_on_an_unrouted_path_cannot_be_exempted_by_a_session(
    db, app_with_db, make_user, monkeypatch
):
    """A known, bounded limit of the `authenticated` short-circuit.

    `scope["state"]["user_id"]` is set by the auth DEPENDENCY, and dependencies
    only run once a route has matched. A request to a path this app serves no
    route for therefore reaches the middleware with no user on it, however good
    the caller's bearer was - so with `signal_api_404` on, a signed-in user
    hitting removed endpoints (a stale SPA bundle after an upgrade, say) is
    counted like an anonymous one.

    Bounded rather than fixed: `signal_api_404` ships off, and when on it still
    requires `min_distinct_paths` (15) distinct paths, so one stale endpoint can
    never reach it. Pinned here so the limit is discovered by a failing test
    rather than by an admin locking themselves out."""
    from app.services import rate_limit

    _enable(db, signal_api_404=True, min_distinct_paths=1)
    make_user(email="u2@test.local", role=UserRole.employee, password=PW)
    monkeypatch.setattr(
        rate_limit, "check_ip_allowed",
        lambda bucket, ip, limit=0, window_sec=0: bucket.startswith("scanblock"),
    )
    monkeypatch.setattr(sg, "_distinct_paths_seen", lambda ip, path, window: 99)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_db, client=(SCANNER, 51234)),
        base_url="http://test",
    ) as ac:
        login = await ac.post(
            "/api/auth/login", json={"email": "u2@test.local", "password": PW}
        )
        token = login.json()["access_token"]
        resp = await ac.get(
            "/api/definitely-not-a-route", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 404
    await _settled()
    assert db.query(IpBlock).filter(IpBlock.subject == SCANNER).count() == 1


@pytest.mark.asyncio
async def test_the_new_block_ceiling_is_honoured(db, scanner_client, monkeypatch):
    """Bounds a forged-header flood: it cannot manufacture unbounded block rows,
    nor unbounded collateral victims."""
    from app.services import rate_limit

    _enable(db)
    monkeypatch.setattr(
        rate_limit, "check_ip_allowed",
        lambda bucket, ip, limit=0, window_sec=0: bucket == "scanblock" and False
        or bucket not in ("scanguard", "scanblock"),
    )
    await scanner_client.get("/.env")
    await _settled()
    assert db.query(IpBlock).count() == 0


@pytest.mark.asyncio
async def test_redis_down_still_serves_everyone(db, scanner_client, monkeypatch):
    """What a Redis outage actually does, stubbed at the seam it actually hits.

    This test used to monkeypatch `rate_limit.check_ip_allowed` itself to raise
    - a call path that cannot occur, because that function catches its own Redis
    errors internally. It therefore exercised `note_offence`'s blanket except,
    not an outage, and the name promised something the code does not do.

    With `get_redis` broken (the real seam), `check_ip_allowed` falls back to
    its in-process counter, so counting continues per-process and a source CAN
    still be blocked. The invariant that does hold, and the one worth pinning,
    is that nobody is refused because of the outage itself: every request is
    still served on its own merits."""
    from app import redis_client

    _enable(db)

    def _boom(*a, **kw):
        raise RuntimeError("redis is down")

    monkeypatch.setattr(redis_client, "get_redis", _boom)
    for _ in range(3):
        assert (await scanner_client.get("/api/health")).status_code == 200
    assert (await scanner_client.get("/.env")).status_code == 404
    await _settled()


# ---------------------------------------------------------------------------
# Brute-force guard, driven through the real login route.
#
# These use the REAL counter (no `check_ip_allowed` stub) with a small
# `auth_threshold`, because the thing most worth pinning is the arithmetic of
# which attempt crosses - and a stubbed counter proves nothing about it.
# ---------------------------------------------------------------------------


# The registry's floor for `auth_threshold`, and the lowest value these tests
# can use: anything smaller is clamped UP, which would silently invalidate every
# "N attempts are served, the N+1th blocks" assertion below. The floor exists
# because below 5 a single user who forgot their password crosses before the
# per-account lockout can convert their failures to uncountable 423s.
AUTH_FLOOR = 5


def _enable_auth_guard(db, threshold=AUTH_FLOOR, **over):
    _enable(
        db,
        signal_probe_path=False,
        signal_auth_failure=True,
        auth_threshold=threshold,
        **over,
    )
    assert sg.snapshot()["auth_threshold"] == threshold, (
        "the registry clamped the threshold; the arithmetic below would be wrong"
    )


class _FakeRedis:
    """Just enough Redis for the counting path, in a dict.

    Deliberately NOT a stub of `check_ip_allowed`: the real limiter and the real
    `clear_counters` both have to act on the SAME store, or a test cannot tell
    "the release cleared the counter" from "the fake never had one". A stubbed
    limiter made `test_releasing_a_block_does_not_leave_it_re_arming` pass while
    proving nothing.

    It is also what keeps these tests off the deployment's Redis: the suite runs
    inside the compose network, so a bare `get_redis()` in code under test
    reaches the live instance.
    """

    def __init__(self) -> None:
        self.store: dict = {}

    def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    def expire(self, key, seconds, nx=False):
        return True

    def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
        return len(keys)

    def sadd(self, key, *values):
        bucket = self.store.setdefault(key, set())
        before = len(bucket)
        bucket.update(values)
        return len(bucket) - before

    def scard(self, key):
        return len(self.store.get(key, set()))

    # --- watchlist structures. Without these, every `_watch_note` call in an
    # end-to-end test died inside its own `except` and the note_offence ->
    # _watch_note wiring was pinned by nothing.
    def pipeline(self):
        return self

    def zincrby(self, key, amt, member):
        bucket = self.store.setdefault(key, {})
        bucket[member] = bucket.get(member, 0) + amt

    def zadd(self, key, mapping):
        self.store.setdefault(key, {}).update(mapping)

    def hset(self, key, field, value):
        self.store.setdefault(key, {})[field] = value

    def zcard(self, key):
        return len(self.store.get(key, {}))

    def zrange(self, key, start, stop):
        ordered = sorted(self.store.get(key, {}), key=lambda m: self.store[key][m])
        return ordered[start:stop + 1]

    def zrangebyscore(self, key, lo, hi):
        cutoff = float(str(hi).lstrip("("))
        return [m for m, sc in self.store.get(key, {}).items() if sc < cutoff]

    def zrem(self, key, *members):
        for m in members:
            self.store.get(key, {}).pop(m, None)

    def hdel(self, key, *fields):
        for f in fields:
            self.store.get(key, {}).pop(f, None)

    def execute(self):
        return [len(self.store.get("fh:scanguard:watch:count", {}))]


@pytest.fixture
def _real_ip_counter(monkeypatch):
    """Run the REAL rate limiter against an in-process Redis."""
    from app.services import rate_limit

    fake = _FakeRedis()
    # rate_limit imports the factory at module scope; scan_guard imports it
    # inside each function. Both have to point at the same fake.
    monkeypatch.setattr(rate_limit, "get_redis", lambda: fake)
    monkeypatch.setattr("app.redis_client.get_redis", lambda: fake)
    monkeypatch.setattr(rate_limit, "check_ip_allowed", _real_check_ip_allowed)
    return fake


@pytest.mark.asyncio
async def test_a_normal_2fa_login_never_earns_an_offence(
    db, app_with_db, make_user, _real_ip_counter
):
    """The defect that made this signal unusable.

    A TOTP-enrolled user posting their password gets 401 TOTP_REQUIRED - the
    normal first step, not a wrong secret. Counting it meant an office with 2FA
    on blocked itself by logging in."""
    from datetime import datetime, timezone

    from app.models.user_totp import UserTOTP

    _enable_auth_guard(db)
    user = make_user(email="tw@test.local", role=UserRole.employee, password=PW)
    db.add(
        UserTOTP(
            user_id=user.id,
            secret_encrypted=b"dummy",
            enabled_at=datetime.now(tz=timezone.utc).replace(tzinfo=None),
            last_used_counter=0,
        )
    )
    db.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app_with_db, client=(SCANNER, 51234)),
        base_url="http://test",
    ) as ac:
        for _ in range(6):
            resp = await ac.post(
                "/api/auth/login", json={"email": "tw@test.local", "password": PW}
            )
            assert resp.status_code == 401
            assert resp.json()["code"] == "TOTP_REQUIRED"
        await _settled()
        assert db.query(IpBlock).count() == 0, "a 2FA login was treated as an attack"

        # Positive control: the same route, same source, with a WRONG password,
        # crosses the same threshold and does block. Without this the test
        # passes even if the whole signal is switched off.
        for _ in range(6):
            await ac.post(
                "/api/auth/login",
                json={"email": "tw@test.local", "password": "WrongPass!123"},
            )
        await _settled()

    assert db.query(IpBlock).filter(IpBlock.subject == SCANNER).count() == 1


@pytest.mark.asyncio
async def test_credential_guessing_blocks_at_the_auth_threshold(
    db, app_with_db, _real_ip_counter
):
    """Guessing against an address with no account: the failures stay
    INVALID_CREDENTIALS (no account to lock, so no 423 conversion)."""
    _enable_auth_guard(db)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_db, client=(SCANNER, 51234)),
        base_url="http://test",
    ) as ac:
        for _ in range(AUTH_FLOOR):
            resp = await ac.post(
                "/api/auth/login",
                json={"email": "nobody@test.local", "password": "Guess!12345"},
            )
            assert resp.status_code == 401
        await _settled()
        # `check_ip_allowed` allows while count <= limit, so exactly `threshold`
        # attempts are still SERVED and the next one crosses. That `<=` is
        # load-bearing: the default of 15 is sized so a three-person office
        # grinding to lockout lands on 15 and is served. "Fixing" it to `<`
        # re-bans them.
        assert db.query(IpBlock).count() == 0

        await ac.post(
            "/api/auth/login",
            json={"email": "nobody@test.local", "password": "Guess!12345"},
        )
        await _settled()
        row = db.query(IpBlock).filter(IpBlock.subject == SCANNER).one()
        assert row.reason == "auth_failure"

        # And the block is a byte-identical 404 on an unrelated route.
        refused = await ac.get("/api/health")
        assert refused.status_code == 404
        assert refused.json()["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_the_scan_and_credential_budgets_do_not_pool(
    db, app_with_db, _real_ip_counter
):
    """Two bait probes plus one password typo must not add up to a block.

    Pooling put the typo - which legitimate users produce constantly - in charge
    of a threshold sized for bait paths, which they never touch."""
    # Scan budget 3, credential budget 5 - deliberately different, which is the
    # whole point: one number cannot serve both.
    _enable(db, signal_probe_path=True, signal_auth_failure=True,
            threshold=3, auth_threshold=AUTH_FLOOR)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_db, client=(SCANNER, 51234)),
        base_url="http://test",
    ) as ac:
        # 2 bait probes (under the scan budget of 3) + 5 credential failures
        # (on the credential budget of 5). Pooled at either limit this blocks.
        for _ in range(2):
            assert (await ac.get("/.env")).status_code == 404
        for _ in range(AUTH_FLOOR):
            await ac.post(
                "/api/auth/login",
                json={"email": "nobody@test.local", "password": "Guess!12345"},
            )
        await _settled()
        assert db.query(IpBlock).count() == 0, "the two budgets pooled"

        # Positive control: one more credential failure crosses the auth budget.
        for _ in range(1):
            await ac.post(
                "/api/auth/login",
                json={"email": "nobody@test.local", "password": "Guess!12345"},
            )
        await _settled()

    assert db.query(IpBlock).filter(IpBlock.subject == SCANNER).count() == 1


@pytest.mark.asyncio
async def test_a_shared_office_with_real_logins_is_not_blocked(
    db, app_with_db, make_user, _real_ip_counter
):
    """The NAT case. Successful logins from the same address, across more than
    one account, explain the failures - so the block is withheld."""
    _enable_auth_guard(db)
    make_user(email="a@office.local", role=UserRole.employee, password=PW)
    make_user(email="b@office.local", role=UserRole.employee, password=PW)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_db, client=(SCANNER, 51234)),
        base_url="http://test",
    ) as ac:
        for email in ("a@office.local", "b@office.local"):
            ok = await ac.post(
                "/api/auth/login", json={"email": email, "password": PW}
            )
            assert ok.status_code == 200, ok.text
        for _ in range(6):
            await ac.post(
                "/api/auth/login",
                json={"email": "a@office.local", "password": "WrongPass!123"},
            )
        await _settled()

    assert db.query(IpBlock).count() == 0, "an office with real logins was blocked"


@pytest.mark.asyncio
async def test_one_account_cannot_launder_a_stuffer(
    db, app_with_db, make_user, _real_ip_counter
):
    """The mirror of the test above, and the reason the exemption needs TWO
    accounts: otherwise anyone holding one valid login scripts successes from
    their own address and grinds every other account for free."""
    _enable_auth_guard(db)
    make_user(email="mine@test.local", role=UserRole.employee, password=PW)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_db, client=(SCANNER, 51234)),
        base_url="http://test",
    ) as ac:
        for _ in range(3):
            ok = await ac.post(
                "/api/auth/login", json={"email": "mine@test.local", "password": PW}
            )
            assert ok.status_code == 200
        for _ in range(6):
            await ac.post(
                "/api/auth/login",
                json={"email": "victim@test.local", "password": "Guess!12345"},
            )
        await _settled()

    assert db.query(IpBlock).filter(IpBlock.subject == SCANNER).count() == 1


@pytest.mark.asyncio
async def test_stale_successes_do_not_launder_todays_failures(
    db, app_with_db, make_user, _real_ip_counter
):
    """Freshness, the same rule network escalation learned: the successes have
    to be inside the window the failures are counted over."""
    from datetime import timedelta

    from app.models.login_attempt import LoginAttempt, LoginOutcome
    from app.utils.timeutil import utc_now

    _enable_auth_guard(db, window_sec=60)
    old = utc_now() - timedelta(seconds=600)
    for email in ("a@office.local", "b@office.local"):
        for _ in range(5):
            db.add(LoginAttempt(
                email=email, ip=SCANNER, attempted_at=old,
                outcome=LoginOutcome.success.value,
            ))
    db.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app_with_db, client=(SCANNER, 51234)),
        base_url="http://test",
    ) as ac:
        for _ in range(6):
            await ac.post(
                "/api/auth/login",
                json={"email": "victim@test.local", "password": "Guess!12345"},
            )
        await _settled()

    assert db.query(IpBlock).filter(IpBlock.subject == SCANNER).count() == 1


@pytest.mark.asyncio
async def test_an_expired_session_refreshing_is_never_brute_force(
    db, app_with_db, _real_ip_counter
):
    """`/api/auth/refresh` 401s once per expired tab, and a bearer-less API call
    401s on every route. Neither is a credential submission - which is why the
    prefixes are exact and `/api/auth/` is never used as a blanket."""
    _enable_auth_guard(db)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_db, client=(SCANNER, 51234)),
        base_url="http://test",
    ) as ac:
        for _ in range(8):
            assert (await ac.post("/api/auth/refresh")).status_code == 401
            assert (await ac.get("/api/shares")).status_code == 401
        await _settled()
        assert db.query(IpBlock).count() == 0

        # Positive control on the same client and settings.
        for _ in range(AUTH_FLOOR + 1):
            await ac.post(
                "/api/auth/login",
                json={"email": "nobody@test.local", "password": "Guess!12345"},
            )
        await _settled()

    assert db.query(IpBlock).filter(IpBlock.subject == SCANNER).count() == 1


@pytest.mark.asyncio
async def test_releasing_a_block_does_not_leave_it_re_arming(
    db, app_with_db, make_user, login_as, _real_ip_counter
):
    """A release must also forget the counter that produced the block.

    Otherwise the source is still sitting at the threshold for the rest of the
    window and the very next offending request re-blocks it within seconds - the
    admin's release looks like it did nothing. Same hair-trigger shape v2.11.0
    fixed for network escalation, one level down."""
    _enable_auth_guard(db)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_db, client=(SCANNER, 51234)),
        base_url="http://test",
    ) as ac:
        for _ in range(AUTH_FLOOR + 1):
            await ac.post(
                "/api/auth/login",
                json={"email": "nobody@test.local", "password": "Guess!12345"},
            )
        await _settled()
        row = db.query(IpBlock).filter(IpBlock.subject == SCANNER).one()

        sg.release(db, block_id=row.id, actor_id=None)
        db.commit()
        sg._reset_cache()

        # One more offending request must NOT immediately re-block.
        await ac.post(
            "/api/auth/login",
            json={"email": "nobody@test.local", "password": "Guess!12345"},
        )
        await _settled()

    live = (
        db.query(IpBlock)
        .filter(IpBlock.subject == SCANNER, IpBlock.released_at.is_(None))
        .count()
    )
    assert live == 0, "the released source was re-blocked off a stale counter"


@pytest.mark.asyncio
async def test_an_offence_puts_the_source_on_the_watchlist_then_takes_it_off(
    db, app_with_db, _real_ip_counter
):
    """The `note_offence -> _watch_note` wiring, end to end.

    Worth its own test because `_watch_note` swallows everything: with a fake
    that lacked the watch methods, every end-to-end write failed silently and
    the whole feature could have been disconnected without a single red test."""
    _enable_auth_guard(db)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_db, client=(SCANNER, 51234)),
        base_url="http://test",
    ) as ac:
        # Under the threshold: watched, not blocked.
        for _ in range(2):
            await ac.post(
                "/api/auth/login",
                json={"email": "nobody@test.local", "password": "Guess!12345"},
            )
        await _settled()
        watched = _real_ip_counter.store.get("fh:scanguard:watch:count", {})
        assert watched.get(SCANNER) == 2
        assert db.query(IpBlock).count() == 0

        # Crossing it graduates the source off the watchlist and into the table.
        for _ in range(AUTH_FLOOR):
            await ac.post(
                "/api/auth/login",
                json={"email": "nobody@test.local", "password": "Guess!12345"},
            )
        await _settled()

    assert db.query(IpBlock).filter(IpBlock.subject == SCANNER).count() == 1
    assert SCANNER not in _real_ip_counter.store.get("fh:scanguard:watch:count", {})


@pytest.mark.asyncio
async def test_a_mapped_address_office_is_still_recognised_as_shared(
    db, app_with_db, make_user, _real_ip_counter
):
    """The two sides of the shared-egress join must use the same address form.

    The guard counts offences against the NORMALISED address
    (`client_ip_from_scope`), so `login_attempts.ip` has to be written the same
    way. It was not: `_request_ip` returned `request.client.host` raw, so on a
    dual-stack deployment passing IPv4-mapped addresses the join found zero
    rows, the office's own successful logins were invisible, and it was blocked
    by the very check that exists to exempt it - silently."""
    mapped = f"::ffff:{SCANNER}"
    _enable_auth_guard(db)
    make_user(email="a@office.local", role=UserRole.employee, password=PW)
    make_user(email="b@office.local", role=UserRole.employee, password=PW)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_db, client=(mapped, 51234)),
        base_url="http://test",
    ) as ac:
        for email in ("a@office.local", "b@office.local"):
            ok = await ac.post(
                "/api/auth/login", json={"email": email, "password": PW}
            )
            assert ok.status_code == 200, ok.text
        for _ in range(AUTH_FLOOR + 1):
            await ac.post(
                "/api/auth/login",
                json={"email": "a@office.local", "password": "WrongPass!123"},
            )
        await _settled()

    assert db.query(IpBlock).count() == 0, (
        "a mapped-address office was blocked despite its own successful logins"
    )
