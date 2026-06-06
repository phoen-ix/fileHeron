"""Pytest fixtures for fileHeron backend.

We configure env vars BEFORE importing any app module so config.Settings
sees test-friendly values, then build a fresh in-memory SQLite DB per test.
"""
from __future__ import annotations

import os

# These must be set before any `from app...` import below.
os.environ.setdefault("ENVIRONMENT", "development")
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
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ADMIN_BOOTSTRAP_EMAIL", "")
os.environ.setdefault("ADMIN_BOOTSTRAP_PASSWORD", "")
os.environ.setdefault("TEST_ACCOUNT_EMAIL", "")
os.environ.setdefault("TEST_ACCOUNT_PASSWORD", "")
os.environ.setdefault("TEST_ACCOUNT_DISPLAY_NAME", "")
os.environ.setdefault("SMTP_HOST", "")  # logs-fallback in services/email.py
os.environ.setdefault("APP_URL", "http://test.fileheron.local")
os.environ.setdefault("APP_NAME", "fileHeron")
# Lower Argon2 cost in tests to keep the suite fast.
os.environ.setdefault("ARGON2_TIME_COST", "1")
os.environ.setdefault("ARGON2_MEMORY_COST_KIB", "8192")
os.environ.setdefault("ARGON2_PARALLELISM", "1")

# Phase 3a — upload pipeline. Tests don't actually run tusd / read disk,
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
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.dependencies import get_db
from app.main import app
from app.models.user import Locale, User, UserRole
from app.utils.crypto import argon2_hash, normalize_email


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
    `.configure(bind=...)` — every module that imported `SessionLocal`
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
    by register-from-invite / forgot-password / verify-email — same
    rationale, same fix."""
    from app.services import rate_limit
    monkeypatch.setattr(rate_limit, "check_login_ip_allowed", lambda *_a, **_kw: True)
    monkeypatch.setattr(rate_limit, "reset_ip_window", lambda ip: None)
    monkeypatch.setattr(
        rate_limit, "check_ip_allowed", lambda *_a, **_kw: True
    )


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
    scheduled inside the event loop with no awaiter — pytest's
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
    session — including audit rows the test then expects to see.
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
    that override wins."""
    from app.services import job_queue

    async def _aenqueue(*_a, **_kw):
        return None

    monkeypatch.setattr(job_queue, "aenqueue", _aenqueue)
    monkeypatch.setattr(job_queue, "enqueue", lambda *_a, **_kw: None)


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
    on whether a given fixture password happens to be in the breach corpus —
    flaky and environment-coupled. We stub the lowest level (`_fetch_range`)
    so the real k-anonymity logic still runs; tests that exercise HIBP
    directly (test_hibp.py) re-monkeypatch `_fetch_range`, and tests that
    want a breach stub `is_password_breached` itself — both override this."""
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
        groups_claim: str = "groups",
        admin_groups: str = "fh-admins",
        employee_groups: str = "fh-employees",
        redirect_uri: str = "",
        enabled: bool = True,
    ) -> OIDCProvider:
        p = OIDCProvider(
            name=name,
            preset=preset,
            issuer_url=issuer_url,
            client_id=client_id,
            client_secret_encrypted=encrypt_setting(client_secret) if client_secret else "",
            groups_claim=groups_claim,
            admin_groups=admin_groups,
            employee_groups=employee_groups,
            redirect_uri=redirect_uri,
            enabled=enabled,
        )
        db.add(p)
        db.commit()
        return p

    return _make
