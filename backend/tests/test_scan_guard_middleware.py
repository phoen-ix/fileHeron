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
    from app.services import settings as settings_svc

    values = {"enabled": True, "signal_probe_path": True}
    values.update(over)
    for key, field in (
        (settings_svc.Keys.SCAN_GUARD_ENABLED, "enabled"),
        (settings_svc.Keys.SCAN_GUARD_SIGNAL_PROBE_PATH, "signal_probe_path"),
    ):
        settings_svc.set_value(
            db, key=key, value="true" if values.get(field) else "false", actor=None
        )
    db.commit()
    sg._reset_cache()


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
    from app.services import rate_limit

    _enable(db)
    make_user(email="u@test.local", role=UserRole.employee, password=PW)
    monkeypatch.setattr(
        rate_limit, "check_ip_allowed",
        lambda bucket, ip, limit=0, window_sec=0: bucket != "scanguard",
    )
    async with AsyncClient(
        transport=ASGITransport(app=app_with_db, client=(SCANNER, 51234)),
        base_url="http://test",
    ) as ac:
        login = await ac.post(
            "/api/auth/login", json={"email": "u@test.local", "password": PW}
        )
        token = login.json()["access_token"]
        for _ in range(5):
            await ac.get(
                "/api/shares/nope", headers={"Authorization": f"Bearer {token}"}
            )
    assert db.query(IpBlock).count() == 0


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
    assert db.query(IpBlock).count() == 0


@pytest.mark.asyncio
async def test_redis_down_blocks_nobody(db, scanner_client, monkeypatch):
    """Fail OPEN. The guard protects nothing that was not already 404ing, so
    failing closed would trade a total outage for zero security gain."""
    from app.services import rate_limit

    _enable(db)

    def _boom(*a, **kw):
        raise RuntimeError("redis is down")

    monkeypatch.setattr(rate_limit, "check_ip_allowed", _boom)
    assert (await scanner_client.get("/.env")).status_code == 404
    assert db.query(IpBlock).count() == 0
