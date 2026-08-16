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
