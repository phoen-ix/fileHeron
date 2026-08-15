"""Pytest fixtures for fileHeron backend.

We configure env vars BEFORE importing any app module so config.Settings
sees test-friendly values, then build a fresh in-memory SQLite DB per test.
"""
from __future__ import annotations

import os

# A handful of settings decide what the suite is even TESTING, so they are
# FORCED rather than defaulted.
#
# `setdefault` is a no-op when the variable is already set, and the app's own
# environment is exactly what a developer is likely to have around - running the
# suite inside the backend image, or with a sourced `.env`, silently swaps in
# production values. `COOKIE_SECURE=true` alone makes every refresh-cookie test
# fail with a bare 401 (the test client will not send a Secure cookie over
# http://test), and `REQUIRE_2FA=admins` 403s every admin-authored request.
# Neither says anything about the code. CI has a bare environment, so main stayed
# green while a local run showed seven unrelated-looking failures.
#
# Infrastructure POINTERS (DB_*, REDIS_URL) stay `setdefault` below: the
# redis-tests and alembic-roundtrip CI jobs legitimately point the suite at real
# services.
# Written as straight assignments, not a loop: ruff's E402 exempts direct
# `os.environ` manipulation before imports, but does not recognise it wrapped in
# a loop, and adding an E402 ignore here would blind the file to real ordering
# mistakes.
os.environ["ENVIRONMENT"] = "development"
os.environ["COOKIE_SECURE"] = "false"
os.environ["REQUIRE_2FA"] = ""
os.environ["ADMIN_BOOTSTRAP_EMAIL"] = ""
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = ""
os.environ["TEST_ACCOUNT_EMAIL"] = ""
os.environ["TEST_ACCOUNT_PASSWORD"] = ""
os.environ["SMTP_HOST"] = ""  # logs-fallback in services/email.py

os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "3306")
os.environ.setdefault("DB_NAME", "fileheron_test")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test_password_unused_for_sqlite_tests")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret_at_least_thirty_two_characters_long_xx")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "15")
os.environ.setdefault("REFRESH_TOKEN_EXPIRE_DAYS", "7")
os.environ.setdefault("TEST_ACCOUNT_DISPLAY_NAME", "")
os.environ.setdefault("APP_URL", "http://test.fileheron.local")
os.environ.setdefault("APP_NAME", "fileHeron")
# Lower Argon2 cost in tests to keep the suite fast.
os.environ.setdefault("ARGON2_TIME_COST", "1")
os.environ.setdefault("ARGON2_MEMORY_COST_KIB", "8192")
os.environ.setdefault("ARGON2_PARALLELISM", "1")

# Phase 3a - upload pipeline. Tests don't actually run tusd / read disk,
# but services/tus_signing.py needs TUS_HOOK_SECRET at import / call time.
os.environ.setdefault(
    "TUS_HOOK_SECRET", "test_tus_hook_secret_at_least_thirty_two_characters_long_xx"
)
os.environ.setdefault("MAX_DIRECT_UPLOAD_BYTES", "104857600")
os.environ.setdefault("STORAGE_ROOT", "/tmp/fileheron-test/files")
os.environ.setdefault("TUS_UPLOAD_DIR", "/tmp/fileheron-test/uploads")
os.environ.setdefault("QUARANTINE_DIR", "/tmp/fileheron-test/quarantine")
os.environ.setdefault("TUS_PUBLIC_BASE", "/uploads/")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.dependencies import get_db
from app.main import app
from app.models.user import Locale, User, UserRole
from app.utils.crypto import argon2_hash, normalize_email


@pytest.fixture
def engine():
    """The default test engine, with foreign keys ENFORCED.

    SQLite ships FK enforcement off. With it off, the whole suite ran against a
    database that silently accepted rows MariaDB would reject, and the ~30
    `ondelete=` declarations on the models were never exercised - so an ORM-level
    cascade could look correct while the DB-level one was wrong, which is
    precisely the class of defect the erasure, purge and config-restore paths
    are made of. `fk_db` existed for exactly this and one test file used it
    (audit 2026-07-30, tests-17).

    Flipped last, deliberately: doing it mid-programme would have made a
    harness-induced failure indistinguishable from a fix-induced one."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _fk_pragma(dbapi_conn, _rec):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def db(session_factory):
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


# ---- FK-enforced engine ----------------------------------------------------
# Kept as its own fixture pair for the tests that name it explicitly. Since the
# 2026-07-30 audit the DEFAULT `engine` enforces foreign keys too, so `fk_db` is
# no longer the only way to get integrity - it is now just an independent
# session on an independent engine.


@pytest.fixture
def fk_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _fk_pragma(dbapi_conn, _rec):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def fk_db(fk_engine):
    factory = sessionmaker(bind=fk_engine, autoflush=False, expire_on_commit=False)
    s = factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def app_with_db(session_factory):
    def _get_db():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    yield app
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app_with_db):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_db), base_url="http://test"
    ) as ac:
        yield ac


# ---- factories -------------------------------------------------------------


@pytest.fixture(autouse=True)
def _rebind_session_local(engine):
    """`SessionLocal` is bound to the prod MySQL engine at module import
    time in `app/database.py`, and ten modules (services/email,
    routers/health, every worker, etc.) import it directly. Any code
    that does `SessionLocal()` instead of going through FastAPI's
    `Depends(get_db)` bypasses the test fixture and hits a real MySQL
    socket that isn't there.

    Rebind the existing sessionmaker to the per-test SQLite engine via
    `.configure(bind=...)` - every module that imported `SessionLocal`
    holds a reference to the SAME sessionmaker instance, so this one
    call retargets all of them in lockstep. Restore on teardown so we
    don't leave a global pointing at a disposed engine.
    """
    from app.database import SessionLocal
    from app.database import engine as prod_engine
    SessionLocal.configure(bind=engine)
    try:
        yield
    finally:
        SessionLocal.configure(bind=prod_engine)


@pytest.fixture(autouse=True)
def _disable_ip_rate_limit(monkeypatch):
    """The per-IP login rate limit is Redis-backed and shared across all
    invocations from 127.0.0.1. Cumulative test attempts would trip it
    quickly, especially when smoke tests against the same backend have
    already burned IP allowance. Override in unit tests; the per-account
    lockout (DB-backed) is what these tests target anyway.

    Also covers the generic `check_ip_allowed(bucket, ip, limit)` used
    by register-from-invite / forgot-password / verify-email - same
    rationale, same fix."""
    from app.services import rate_limit
    monkeypatch.setattr(rate_limit, "check_login_ip_allowed", lambda *_a, **_kw: True)
    monkeypatch.setattr(rate_limit, "reset_ip_window", lambda ip: None)
    monkeypatch.setattr(
        rate_limit, "check_ip_allowed", lambda *_a, **_kw: True
    )
    # Same rationale for the per-USER re-auth throttle (step-up). Its Redis
    # fallback `_local_allow` keeps state in a module-level dict, so without
    # this a file's cumulative wrong-password tests trip the limit and later
    # tests see 429 instead of the 403 they assert. Tests that target the
    # throttle itself re-enable it explicitly (see test_step_up_gates.py).
    monkeypatch.setattr(rate_limit, "check_user_allowed", lambda *_a, **_kw: True)
    monkeypatch.setattr(rate_limit, "reset_user_window", lambda *_a, **_kw: None)


@pytest.fixture(autouse=True)
def _isolated_transfer_marks(monkeypatch):
    """Give transfer_activity a per-test in-memory Redis.

    Its paid/serving marks go through a bare `get_redis()`, and the suite runs
    inside the compose network - so without this the marks are written to the
    LIVE Redis, where they both pollute production and leak BETWEEN tests: the
    keys embed user and file ids, which restart at 1 for every test against the
    in-memory DB, so one test's payment silently credits the next one's.
    """
    from app.services import transfer_activity

    store: dict[str, str] = {}

    class _Fake:
        def set(self, key, value, ex=None, nx=False):
            if nx and key in store:
                return None
            store[key] = value
            return True

        def get(self, key):
            return store.get(key)

        def delete(self, *keys):
            for k in keys:
                store.pop(k, None)

        def zadd(self, key, mapping):
            store.setdefault(key, {})
            store[key].update(mapping)

        def zrem(self, key, *members):
            for m in members:
                store.get(key, {}).pop(m, None)

        def zcard(self, key):
            return len(store.get(key, {}))

        def zremrangebyscore(self, key, lo, hi):
            d = store.get(key, {})
            for m in [m for m, sc in d.items() if lo <= sc <= hi]:
                d.pop(m, None)

        def zscore(self, key, member):
            return store.get(key, {}).get(member)

    monkeypatch.setattr(transfer_activity, "get_redis", lambda: _Fake())
    yield
    store.clear()


@pytest.fixture(autouse=True)
def _reset_oidc_caches():
    """Discovery + JWKS caches are process-global; reset between tests
    so stale entries from one provider don't bleed into another."""
    from app.services import jwks as jwks_svc
    from app.services import oidc as oidc_svc
    oidc_svc._DISCOVERY_CACHE.clear()
    jwks_svc._reset_cache()
    yield
    oidc_svc._DISCOVERY_CACHE.clear()
    jwks_svc._reset_cache()


@pytest.fixture(autouse=True)
def _no_op_sse_publish(monkeypatch):
    """SSE delivery is fire-and-forget against Redis. In tests Redis
    isn't always reachable, and even when it is the publish task is
    scheduled inside the event loop with no awaiter - pytest's
    unraisable-exception hook flags the GeneratorExit at teardown.
    Patch the publish helpers to no-ops; the durable in-app
    `notifications` row is what tests actually assert on."""
    from app.services import sse
    monkeypatch.setattr(sse, "publish_sync", lambda *_a, **_kw: None)
    monkeypatch.setattr(sse, "publish_admin_sync", lambda *_a, **_kw: None)

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(sse, "publish", _noop)
    monkeypatch.setattr(sse, "publish_admin", _noop)


@pytest.fixture(autouse=True)
def _no_op_email_send(monkeypatch):
    """`services/email.py::_send_resolved` opens its own `SessionLocal()`
    to look up SMTP config. With our SQLite + StaticPool test setup all
    sessions share the one connection, and the side session's
    close()-time rollback wipes pending writes from the main request
    session - including audit rows the test then expects to see.
    Skip the side-session entirely in tests; the email body is already
    rendered by the caller, so we just no-op the actual send."""
    from app.services import email as email_svc

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(email_svc, "_send_resolved", _noop)


@pytest.fixture(autouse=True)
def _no_op_job_queue(monkeypatch):
    """Same shape as `_no_op_sse_publish`: notification dispatch now
    enqueues a `send_email_job` for every recipient (because we always
    have `email_to=user.email` after the plaintext-email refactor).
    The enqueue path opens an httpx pool against Redis at runtime;
    Redis isn't reachable from the unit-test sandbox so the coroutine
    fails to await cleanly and pytest flags an unraisable warning per
    test. Tests that actually want to assert on enqueued jobs (see
    test_notification_dispatch.py + test_quarantine_admin_notify.py)
    re-monkeypatch `app.services.notification.job_queue.enqueue` and
    that override wins.

    The batch entry points need the same treatment - leaving
    `enqueue_many` live would let every notification test reach for
    Redis through the batched fan-out instead. It delegates to
    `enqueue` rather than no-op'ing, resolved at CALL time, so a test
    that patches `job_queue.enqueue` to collect jobs sees a batch as
    its constituent jobs and does not care which path produced them.
    (`test_enqueue_batching.py` is the exception: it restores the real
    functions, because the pool count is its whole subject.)"""
    from app.services import job_queue

    async def _aenqueue(*_a, **_kw):
        return None

    def _enqueue_many(jobs):
        for name, args, kwargs in jobs:
            job_queue.enqueue(name, *args, **kwargs)

    monkeypatch.setattr(job_queue, "aenqueue", _aenqueue)
    monkeypatch.setattr(job_queue, "enqueue", lambda *_a, **_kw: None)
    monkeypatch.setattr(job_queue, "aenqueue_many", _aenqueue)
    monkeypatch.setattr(job_queue, "enqueue_many", _enqueue_many)


@pytest.fixture(autouse=True)
def _reset_storage_backend():
    """The storage backend is a cached singleton chosen by STORAGE_BACKEND. Reset
    it after every test so a test that selects `s3` can't leak that backend into
    the next (which expects the default local backend)."""
    yield
    from app.services.storage_backend import reset_storage_backend_cache
    reset_storage_backend_cache()


@pytest.fixture(autouse=True)
def _hibp_offline(monkeypatch):
    """Default the HIBP breach check to fail-open (not breached) for the
    whole suite. `is_password_breached` is now enforced on every
    password-set path (register / setup / reset / change), so without this
    the tests would depend on live network access to pwnedpasswords.com and
    on whether a given fixture password happens to be in the breach corpus -
    flaky and environment-coupled. We stub the lowest level (`_fetch_range`)
    so the real k-anonymity logic still runs; tests that exercise HIBP
    directly (test_hibp.py) re-monkeypatch `_fetch_range`, and tests that
    want a breach stub `is_password_breached` itself - both override this."""
    from app.services import hibp as hibp_svc

    async def _no_range(_prefix5):
        return None

    monkeypatch.setattr(hibp_svc, "_fetch_range", _no_range)


@pytest.fixture
def make_user(db):
    """Create a verified user with a known password. Returns the User object."""
    def _make(
        *,
        email: str = "user@test.local",
        password: str = "TestPassword123!",
        display_name: str = "Test User",
        role: UserRole = UserRole.client,
        locale: Locale = Locale.en,
        email_verified: bool = True,
        is_disabled: bool = False,
    ) -> User:
        u = User(
            email=normalize_email(email),
            password_hash=argon2_hash(password),
            display_name=display_name,
            role=role,
            locale=locale,
            email_verified=email_verified,
            is_disabled=is_disabled,
        )
        db.add(u)
        db.commit()
        return u

    return _make


@pytest.fixture
def login_as(client):
    """Issue a real login via the API and return (access_token, cookies)."""
    async def _login(email: str, password: str) -> tuple[str, dict]:
        resp = await client.post("/api/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        return data["access_token"], dict(resp.cookies)

    return _login


@pytest.fixture
def auth_settings():
    """Expose Settings instance for direct use in tests (e.g., manual JWT)."""
    from app.config import settings
    return settings


@pytest.fixture
def make_provider(db):
    """Create an OIDC provider row for tests."""
    from app.models.oidc_provider import OIDCPreset, OIDCProvider
    from app.utils.crypto import encrypt_setting

    def _make(
        *,
        name: str = "Test Provider",
        preset: OIDCPreset = OIDCPreset.custom,
        issuer_url: str = "https://idp.example.com/realms/fh",
        client_id: str = "fileheron",
        client_secret: str = "shh",
        redirect_uri: str = "",
        enabled: bool = True,
    ) -> OIDCProvider:
        p = OIDCProvider(
            name=name,
            preset=preset,
            issuer_url=issuer_url,
            client_id=client_id,
            client_secret_encrypted=encrypt_setting(client_secret) if client_secret else "",
            redirect_uri=redirect_uri,
            enabled=enabled,
        )
        db.add(p)
        db.commit()
        return p

    return _make
