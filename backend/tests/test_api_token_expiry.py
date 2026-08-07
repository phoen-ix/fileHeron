"""Optional API-token expiry (v1.5.7).

NULL expires_at = never expires. A set expiry in the past makes verify_token
reject the token with API_TOKEN_EXPIRED, surfaces as the "expired" status, and
is filterable in the admin inventory. Creation refuses a past expiry.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.middleware.errors import AppError
from app.models.api_token import ApiToken
from app.models.user import UserRole
from app.services import api_token as api_token_svc


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_verify_rejects_expired_token(make_user, db):
    user = make_user(email="u@test.local")
    _rec, plaintext = api_token_svc.create_token(
        db, owner=user, name="t", expires_at=_utcnow() - timedelta(hours=1)
    )
    db.commit()
    with pytest.raises(AppError) as exc:
        api_token_svc.verify_token(db, token_str=plaintext)
    assert exc.value.code == "API_TOKEN_EXPIRED"
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_allows_future_and_null_expiry(make_user, db):
    user = make_user(email="u@test.local")
    _r1, p_future = api_token_svc.create_token(
        db, owner=user, name="future", expires_at=_utcnow() + timedelta(days=7)
    )
    _r2, p_never = api_token_svc.create_token(db, owner=user, name="never")
    db.commit()
    assert api_token_svc.verify_token(db, token_str=p_future).name == "future"
    assert api_token_svc.verify_token(db, token_str=p_never).expires_at is None


@pytest.mark.asyncio
async def test_create_via_api_persists_expiry(make_user, db, client, login_as):
    make_user(email="hr@test.local", password="LongCorrectHorse123!", role=UserRole.employee)
    token, _ = await login_as("hr@test.local", "LongCorrectHorse123!")
    expires = (_utcnow() + timedelta(days=30)).isoformat()
    resp = await client.post(
        "/api/account/api-tokens",
        json={"name": "ci", "expires_at": expires, "password": "LongCorrectHorse123!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["expires_at"] is not None
    db.expire_all()
    row = db.query(ApiToken).filter(ApiToken.id == resp.json()["id"]).one()
    assert row.expires_at is not None


@pytest.mark.asyncio
async def test_create_via_api_rejects_past_expiry(make_user, client, login_as):
    make_user(email="hr@test.local", password="LongCorrectHorse123!", role=UserRole.employee)
    token, _ = await login_as("hr@test.local", "LongCorrectHorse123!")
    past = (_utcnow() - timedelta(days=1)).isoformat()
    resp = await client.post(
        "/api/account/api-tokens",
        json={"name": "ci", "expires_at": past, "password": "LongCorrectHorse123!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "INVALID_EXPIRY"


@pytest.mark.asyncio
async def test_create_via_api_no_expiry_is_unlimited(make_user, db, client, login_as):
    make_user(email="hr@test.local", password="LongCorrectHorse123!", role=UserRole.employee)
    token, _ = await login_as("hr@test.local", "LongCorrectHorse123!")
    resp = await client.post(
        "/api/account/api-tokens",
        json={"name": "ci", "password": "LongCorrectHorse123!"},  # no expires_at
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["expires_at"] is None


@pytest.mark.asyncio
async def test_admin_list_filters_by_expired_status(make_user, db):
    owner = make_user(email="u@test.local")
    expired, _ = api_token_svc.create_token(
        db, owner=owner, name="old", expires_at=_utcnow() - timedelta(hours=1)
    )
    api_token_svc.create_token(db, owner=owner, name="live")  # never expires
    db.commit()

    rows, total = api_token_svc.list_all_tokens(db, status="expired")
    assert total == 1
    assert [r.id for r in rows] == [expired.id]

    # The "active" filter must exclude the expired one.
    active_rows, _ = api_token_svc.list_all_tokens(db, status="active")
    assert expired.id not in {r.id for r in active_rows}


@pytest.mark.asyncio
async def test_current_endpoint_includes_expiry(make_user, db, client):
    user = make_user(email="u@test.local", role=UserRole.employee)
    _rec, plaintext = api_token_svc.create_token(
        db, owner=user, name="desktop", expires_at=_utcnow() + timedelta(days=7)
    )
    db.commit()
    resp = await client.get(
        "/api/account/api-tokens/current",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["expires_at"] is not None
    assert body["status"] == "active"
