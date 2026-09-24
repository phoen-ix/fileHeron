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


# --- audit 2026-09-24: the wizard was first-come-first-served ----------------

_FIRST_ADMIN = {
    "email": "first.admin@test.local",
    "password": "AdminPassword123!",
    "display_name": "First Admin",
}


@pytest.mark.asyncio
async def test_a_configured_setup_token_is_required(client, db, monkeypatch):
    """install.sh brings the stack up behind the public proxy and only THEN
    tells the operator to visit /setup, so whoever got there first owned the
    instance. With SETUP_TOKEN set the wizard wants it."""
    from app.config import settings
    from app.models.user import User

    monkeypatch.setattr(settings, "SETUP_TOKEN", "s3cret-setup-token")

    status = await client.get("/api/setup/status")
    assert status.json() == {"required": True, "token_required": True}

    for bad in (None, "", "wrong", "s3cret-setup-token "):
        body = dict(_FIRST_ADMIN)
        if bad is not None:
            body["setup_token"] = bad
        r = await client.post("/api/setup/admin", json=body)
        assert r.status_code == 403, (bad, r.text)
        assert r.json()["code"] == "SETUP_TOKEN_INVALID"
    assert db.query(User).count() == 0

    r = await client.post(
        "/api/setup/admin", json={**_FIRST_ADMIN, "setup_token": "s3cret-setup-token"}
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_without_a_setup_token_the_wizard_is_unchanged(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "SETUP_TOKEN", "")
    status = await client.get("/api/setup/status")
    assert status.json() == {"required": True, "token_required": False}
    r = await client.post("/api/setup/admin", json=_FIRST_ADMIN)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_completed_setup_does_not_advertise_a_token(client, make_user, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "SETUP_TOKEN", "s3cret-setup-token")
    make_user(email="adm@test.local", role=UserRole.admin)
    status = await client.get("/api/setup/status")
    assert status.json() == {"required": False, "token_required": False}


def test_install_generates_the_setup_token_before_the_stack_comes_up(tmp_path):
    """The token only helps if it exists before `docker compose up -d` makes the
    wizard reachable, and if the operator is handed the URL that carries it.
    Runs install.sh's own `gen_secret` against a scratch .env."""
    import re
    import shutil
    import subprocess
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "install.sh").read_text(encoding="utf-8")
    assert src.index("gen_secret SETUP_TOKEN") < src.index("docker compose up -d")
    assert "/setup?token=$(grep -E '^SETUP_TOKEN=' .env" in src
    example = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^SETUP_TOKEN=$", example, re.M)

    helpers = src[src.index("_secure_env_files() {"):src.index('echo "[install] generating any missing secrets"')]
    (tmp_path / ".env").write_text("SETUP_TOKEN=\n")
    bash = shutil.which("bash")
    assert bash
    # S603: an absolute bash running this repo's own script text.
    subprocess.run(  # noqa: S603
        [bash, "-c", helpers + "gen_secret SETUP_TOKEN\n"],
        cwd=tmp_path, check=True, capture_output=True, text=True, timeout=30,
    )
    env = (tmp_path / ".env").read_text()
    assert re.fullmatch(r"SETUP_TOKEN=[0-9a-f]{64}\n", env), env
