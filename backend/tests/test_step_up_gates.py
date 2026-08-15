"""Re-auth on the destructive / secret-revealing admin surfaces.

An admin access token alone used to be enough to export every secret the
installation holds, to replace the whole configuration, and to erase an account
irreversibly - while the *recoverable* self-update routes had re-prompted for
the password since they shipped. The gate was not absent by design, it was
inconsistent, so a stolen admin session bought strictly more than an admin
normally spends without re-typing their password.

These tests pin the gate on all three, because a re-auth check is exactly the
kind of thing that gets dropped in a refactor without anything going red.
"""
from __future__ import annotations

import pytest

from app.models.user import UserRole

# Captured at import time, i.e. before conftest's autouse fixture neutralises
# them, so the throttle tests can put the real implementations back.
from app.services.rate_limit import check_user_allowed as _real_check_user_allowed
from app.services.rate_limit import reset_user_window as _real_reset_user_window

PW = "Pass12345678!"
WRONG = "not-the-password"


async def _admin(make_user, login_as, email="admin@test.local"):
    make_user(email=email, role=UserRole.admin, password=PW)
    token, _ = await login_as(email, PW)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_backup_export_refuses_without_the_password(make_user, client, login_as):
    headers = await _admin(make_user, login_as)
    resp = await client.post(
        "/api/admin/backup/export",
        json={"categories": ["settings_branding"], "secret_mode": "exclude"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_backup_export_refuses_a_wrong_password(make_user, client, login_as):
    headers = await _admin(make_user, login_as)
    resp = await client.post(
        "/api/admin/backup/export",
        json={
            "categories": ["settings_branding"],
            "secret_mode": "exclude",
            "password": WRONG,
        },
        headers=headers,
    )
    # 403, not 401: the admin IS authenticated. A 401 would trip the SPA's
    # token-refresh interceptor, which would silently retry with the same wrong
    # password and show the user nothing at all.
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "INVALID_PASSWORD"


@pytest.mark.asyncio
async def test_erase_refuses_a_wrong_password(make_user, db, client, login_as):
    headers = await _admin(make_user, login_as)
    target = make_user(email="victim@test.local", role=UserRole.client, password=PW)
    db.commit()
    resp = await client.post(
        f"/api/admin/users/{target.id}/erase",
        json={"password": WRONG},
        headers=headers,
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "INVALID_PASSWORD"
    # And the account is untouched - the gate runs before anything irreversible.
    db.refresh(target)
    assert not target.email.endswith("@erased.invalid")


@pytest.mark.asyncio
async def test_erase_refuses_without_a_body(make_user, db, client, login_as):
    headers = await _admin(make_user, login_as)
    target = make_user(email="victim@test.local", role=UserRole.client, password=PW)
    db.commit()
    resp = await client.post(
        f"/api/admin/users/{target.id}/erase", headers=headers
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_admin_minting_a_token_for_someone_else_refuses_a_wrong_password(
    make_user, db, client, login_as
):
    """The self-service gate is worthless on its own: this route mints a token
    for ANY user, so a stolen admin session would simply target its victim
    instead of itself."""
    headers = await _admin(make_user, login_as)
    target = make_user(email="victim@test.local", role=UserRole.client, password=PW)
    db.commit()
    resp = await client.post(
        "/api/admin/api-tokens",
        json={"target_user_id": target.id, "name": "x", "password": WRONG},
        headers=headers,
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "INVALID_PASSWORD"


@pytest.mark.asyncio
async def test_backup_import_refuses_a_wrong_password(make_user, client, login_as):
    headers = await _admin(make_user, login_as)
    resp = await client.post(
        "/api/admin/backup/import",
        files={"file": ("b.fhbackup.json", b"{}", "application/json")},
        data={"confirm": "true", "password": WRONG},
        headers=headers,
    )
    # Refused on the password BEFORE the artifact is parsed or applied.
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "INVALID_PASSWORD"


# --- throttle + audit ------------------------------------------------------
#
# The gate was an unlimited, unlogged password oracle: a hijacked session could
# guess forever against config-backup export (the only admin surface that reads
# secrets back OUT), erasure, API-token minting and self-update, and each guess
# was a 64 MiB Argon2id verify.


class _FakeRedis:
    """Just the three calls the throttle makes. The suite runs inside the
    compose network, so a bare get_redis() reaches the LIVE Redis - a test that
    exercises a counter must never depend on, or write to, that."""

    def __init__(self):
        self.store = {}

    def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    def expire(self, key, _sec):
        return key in self.store

    def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def real_step_up_throttle(monkeypatch):
    """Undo conftest's autouse neutralisation for the tests that target the
    throttle, and give it an isolated in-memory Redis."""
    from app.services import rate_limit as rl

    monkeypatch.setattr(rl, "check_user_allowed", _real_check_user_allowed)
    monkeypatch.setattr(rl, "reset_user_window", _real_reset_user_window)
    fake = _FakeRedis()  # ONE instance, or every call gets a fresh counter
    monkeypatch.setattr(rl, "get_redis", lambda: fake)
    with rl._local_lock:
        rl._local_windows.clear()
    yield
    with rl._local_lock:
        rl._local_windows.clear()


async def _export_with(client, headers, password):
    return await client.post(
        "/api/admin/backup/export",
        json={
            "categories": ["settings_branding"],
            "secret_mode": "exclude",
            "password": password,
        },
        headers=headers,
    )


@pytest.mark.asyncio
async def test_repeated_wrong_passwords_are_throttled(make_user, client, login_as, db, real_step_up_throttle):
    from app.models.user import User
    from app.services import settings_registry

    headers = await _admin(make_user, login_as, email="throttle@test.local")
    limit = int(settings_registry.effective(db, settings_registry.K.LOCKOUT_THRESHOLD))

    codes = []
    for _ in range(limit + 1):
        resp = await _export_with(client, headers, WRONG)
        codes.append(resp.status_code)

    assert codes[0] == 403, codes          # first attempts are ordinary refusals
    assert codes[-1] == 429, codes         # the one past the limit is throttled

    # And crucially it is NOT the login lockout: a hijacked session must not be
    # able to lock the real admin out of the login page by failing step-up.
    # That blast radius is what commit 2b2117a had to undo in production.
    user = db.query(User).filter(User.email == "throttle@test.local").one()
    db.refresh(user)
    assert user.locked_until is None


@pytest.mark.asyncio
async def test_a_failed_step_up_leaves_an_audit_row(make_user, client, login_as, db, real_step_up_throttle):
    from app.models.audit_log import AuditEventType, AuditLog

    headers = await _admin(make_user, login_as, email="audited@test.local")
    resp = await _export_with(client, headers, WRONG)
    assert resp.status_code == 403

    # The row has to survive the 403: AppError aborts the request, so an
    # uncommitted audit row would roll back and the failure would leave no
    # trace - which is the state this change exists to end.
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.step_up_failed.value)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].extra["reason"] == "bad_password"


@pytest.mark.asyncio
async def test_a_correct_password_clears_the_window(make_user, client, login_as, db, real_step_up_throttle):
    """Ordinary use must never accumulate toward the limit - otherwise an admin
    who mistypes once a week eventually cannot export a backup at all."""
    from app.services import settings_registry

    headers = await _admin(make_user, login_as, email="clears@test.local")
    limit = int(settings_registry.effective(db, settings_registry.K.LOCKOUT_THRESHOLD))

    for _ in range(limit - 1):
        assert (await _export_with(client, headers, WRONG)).status_code == 403
    assert (await _export_with(client, headers, PW)).status_code == 200

    # Budget reset by the success, so the next wrong guess is a plain 403.
    assert (await _export_with(client, headers, WRONG)).status_code == 403
