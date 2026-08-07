"""API token CRUD + verification."""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.api_token import ApiToken
from app.models.user import UserRole
from app.services import api_token as api_token_svc


@pytest.mark.asyncio
async def test_create_returns_plaintext_only_once(make_user, db):
    user = make_user(email="alice@test.local")
    record, plaintext = api_token_svc.create_token(db, owner=user, name="ci-token")
    db.commit()

    assert plaintext.startswith("fh_")
    # Use maxsplit=2 because the base64url secret may itself contain "_".
    parts = plaintext.split("_", 2)
    assert len(parts) == 3
    assert parts[1] == record.prefix
    assert plaintext.endswith(record.last4)
    # The DB stores hash, not plaintext.
    assert record.secret_hash != plaintext


@pytest.mark.asyncio
async def test_verify_token_returns_record(make_user, db):
    user = make_user(email="alice@test.local")
    _record, plaintext = api_token_svc.create_token(db, owner=user, name="t")
    db.commit()

    verified = api_token_svc.verify_token(db, token_str=plaintext)
    assert verified.owner_user_id == user.id
    assert verified.last_used_at is not None


@pytest.mark.asyncio
async def test_verify_token_rejects_unknown_prefix(db):
    with pytest.raises(AppError) as exc:
        api_token_svc.verify_token(db, token_str="fh_deadbeef_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert exc.value.code == "INVALID_API_TOKEN"


@pytest.mark.asyncio
async def test_verify_token_rejects_wrong_secret(make_user, db):
    user = make_user(email="alice@test.local")
    record, plaintext = api_token_svc.create_token(db, owner=user, name="t")
    db.commit()
    # Same prefix, different secret.
    bad = f"fh_{record.prefix}_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    with pytest.raises(AppError) as exc:
        api_token_svc.verify_token(db, token_str=bad)
    assert exc.value.code == "INVALID_API_TOKEN"


@pytest.mark.asyncio
async def test_revoked_token_rejected(make_user, db):
    user = make_user(email="alice@test.local")
    record, plaintext = api_token_svc.create_token(db, owner=user, name="t")
    api_token_svc.revoke_token(db, owner=user, token_id=record.id)
    db.commit()

    with pytest.raises(AppError) as exc:
        api_token_svc.verify_token(db, token_str=plaintext)
    assert exc.value.code == "INVALID_API_TOKEN"


@pytest.mark.asyncio
async def test_list_tokens_excludes_revoked(make_user, db):
    user = make_user(email="alice@test.local")
    rec1, _ = api_token_svc.create_token(db, owner=user, name="a")
    rec2, _ = api_token_svc.create_token(db, owner=user, name="b")
    api_token_svc.revoke_token(db, owner=user, token_id=rec1.id)
    db.commit()

    rows = api_token_svc.list_tokens(db, owner=user)
    assert {r.id for r in rows} == {rec2.id}


@pytest.mark.asyncio
async def test_last_used_persists_on_get_request(make_user, db, client):
    """Regression: a read-only GET with an API token must COMMIT last_used_at.

    get_db never commits (rolls back on close), so the old bare flush() in
    verify_token was discarded on GETs - last_used_at only advanced on write
    endpoints. Here we read the row back after the request (under StaticPool a
    rolled-back flush would have reverted it) and assert it persisted.
    """
    user = make_user(email="u@test.local")
    record, plaintext = api_token_svc.create_token(db, owner=user, name="t")
    db.commit()
    assert record.last_used_at is None

    resp = await client.get(
        "/api/account/me", headers={"Authorization": f"Bearer {plaintext}"}
    )
    assert resp.status_code == 200, resp.text

    db.expire_all()
    row = db.query(ApiToken).filter(ApiToken.id == record.id).one()
    assert row.last_used_at is not None  # committed, not rolled back on close


@pytest.mark.asyncio
async def test_create_via_api(make_user, client, db):
    make_user(email="alice@test.local", password="LongCorrectHorse123!", role=UserRole.employee)
    login = await client.post(
        "/api/auth/login", json={"email": "alice@test.local", "password": "LongCorrectHorse123!"}
    )
    access = login.json()["access_token"]
    resp = await client.post(
        "/api/account/api-tokens",
        json={"name": "ci", "password": "LongCorrectHorse123!"},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["plaintext_token"].startswith("fh_")
    assert body["last4"] == body["plaintext_token"][-4:]

    # The DB row exists.
    db.expire_all()
    row = db.query(ApiToken).filter(ApiToken.id == body["id"]).one()
    assert row.name == "ci"
