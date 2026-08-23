"""v1.0.0 first-admin setup wizard: anonymous /setup/status + one-shot
/setup/admin. Lockout after first admin exists."""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.user import UserRole


@pytest.mark.asyncio
async def test_status_required_on_empty_db(client):
    r = await client.get("/api/setup/status")
    assert r.status_code == 200
    assert r.json()["required"] is True


@pytest.mark.asyncio
async def test_complete_setup_creates_admin(client, db):
    r = await client.post(
        "/api/setup/admin",
        json={
            "email": "first.admin@test.local",
            "password": "AdminPassword123!",
            "display_name": "First Admin",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == "first.admin@test.local"
    assert body["user_id"]

    from app.models.user import User
    db.expire_all()
    user = db.query(User).filter(User.email == "first.admin@test.local").one()
    assert user.role == UserRole.admin
    assert user.email_verified is True
    assert user.is_disabled is False


@pytest.mark.asyncio
async def test_status_flips_to_false_after_first_admin(client, db, make_user):
    make_user(email="adm@test.local", role=UserRole.admin)
    r = await client.get("/api/setup/status")
    assert r.json()["required"] is False


@pytest.mark.asyncio
async def test_second_setup_call_is_locked(client, db, make_user):
    make_user(email="adm@test.local", role=UserRole.admin)
    r = await client.post(
        "/api/setup/admin",
        json={
            "email": "second.admin@test.local",
            "password": "AdminPassword123!",
            "display_name": "Second",
        },
    )
    assert r.status_code == 409
    assert r.json()["code"] == "SETUP_ALREADY_COMPLETE"


@pytest.mark.asyncio
async def test_weak_password_rejected(client, db):
    r = await client.post(
        "/api/setup/admin",
        json={
            "email": "first.admin@test.local",
            "password": "short",
            "display_name": "First",
        },
    )
    assert r.status_code == 422  # Pydantic min_length=12


@pytest.mark.asyncio
async def test_breached_password_rejected(client, db, monkeypatch):
    """HIBP enforcement also covers the first-admin wizard: a valid-length
    but breached password is refused (422 PASSWORD_BREACHED)."""
    from app.services import hibp as hibp_svc

    async def _breached(_pw, _db=None):
        return True

    monkeypatch.setattr(hibp_svc, "is_password_breached", _breached)
    r = await client.post(
        "/api/setup/admin",
        json={
            "email": "first.admin@test.local",
            "password": "BreachedPassword123!",
            "display_name": "First",
        },
    )
    assert r.status_code == 422
    assert r.json()["code"] == "PASSWORD_BREACHED"


@pytest.mark.asyncio
async def test_duplicate_email_rejected(client, db, make_user):
    make_user(email="taken@test.local", role=UserRole.client)
    r = await client.post(
        "/api/setup/admin",
        json={
            "email": "taken@test.local",
            "password": "AdminPassword123!",
            "display_name": "Conflict",
        },
    )
    assert r.status_code == 409
    assert r.json()["code"] == "EMAIL_TAKEN"


# --- the anonymous surface --------------------------------------------------


@pytest.mark.asyncio
async def test_the_setup_route_is_rate_limited(client, db, monkeypatch):
    """Before the first admin exists this is an anonymous POST that runs an
    Argon2id hash (64 MiB) and an outbound HIBP lookup, and it had no limiter at
    all - the only anonymous POST in the app without one. `is_setup_complete`
    short-circuits it on a configured instance, but an instance is reachable
    from the moment compose comes up."""
    from app.services import rate_limit

    calls: list[tuple[str, str]] = []

    def _deny(bucket, ip, *, limit, window_sec):
        calls.append((bucket, ip))
        return False

    monkeypatch.setattr(rate_limit, "check_ip_allowed", _deny)
    r = await client.post(
        "/api/setup/admin",
        json={
            "email": "first@test.local",
            "password": "a-long-enough-password",
            "display_name": "First",
        },
    )
    assert r.status_code == 429
    assert r.json()["code"] == "RATE_LIMITED"
    assert calls and calls[0][0] == "setup_admin"


@pytest.mark.asyncio
async def test_a_taken_email_is_refused_before_the_breach_check(
    db, make_user, monkeypatch
):
    """Ordering, not just outcome: `assert_password_not_breached` makes an
    outbound request to api.pwnedpasswords.com, so running it first let an
    anonymous caller drive that request with an email already destined for 409.
    """
    from app.services import setup as setup_svc

    called = False

    async def _spy(_db, _pw):
        nonlocal called
        called = True

    monkeypatch.setattr(setup_svc, "assert_password_not_breached", _spy)
    # A non-admin, so `is_setup_complete` does not short-circuit before the
    # ordering under test is reached.
    existing = make_user(email="taken@test.local", role=UserRole.client)
    db.commit()

    with pytest.raises(AppError) as exc:
        await setup_svc.complete_setup(
            db,
            email=existing.email,
            password="a-long-enough-password",
            display_name="Dup",
        )
    assert exc.value.code in ("EMAIL_TAKEN", "SETUP_ALREADY_COMPLETE")
    assert not called, "the outbound breach check ran for an email we were rejecting"
