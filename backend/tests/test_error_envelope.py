"""Every 4xx/5xx response must use the same envelope shape:
    { "error": str, "code": str, "details": optional, "request_id": optional }
"""
from __future__ import annotations

import pytest


def _is_envelope(body: dict) -> bool:
    return isinstance(body.get("error"), str) and isinstance(body.get("code"), str)


@pytest.mark.asyncio
async def test_invalid_credentials(client):
    r = await client.post(
        "/api/auth/login", json={"email": "ghost@test.local", "password": "GhostPassword123!"}
    )
    assert r.status_code == 401
    assert _is_envelope(r.json())
    assert r.json()["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_invalid_refresh_no_cookie(client):
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 401
    assert _is_envelope(r.json())
    assert r.json()["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_invalid_invite_token(client):
    r = await client.post(
        "/api/auth/register-from-invite",
        json={
            "token": "totally_made_up_token_string",
            "password": "Whatever123Pass!",
            "display_name": "Imposter",
            "locale": "en",
        },
    )
    assert r.status_code == 404
    assert _is_envelope(r.json())
    assert r.json()["code"] == "INVITE_INVALID"


@pytest.mark.asyncio
async def test_unauthenticated_account_me(client):
    r = await client.get("/api/account/me")
    assert r.status_code == 401
    assert _is_envelope(r.json())


@pytest.mark.asyncio
async def test_validation_error_has_envelope_shape_or_422(client):
    """Pydantic validation errors are FastAPI's default 422 - these don't use
    the AppError envelope but DO have a stable shape (the FastAPI {"detail":[…]}
    structure). This test pins that contract."""
    r = await client.post("/api/auth/login", json={"email": "not-an-email"})
    assert r.status_code == 422
    body = r.json()
    assert "detail" in body
    assert isinstance(body["detail"], list)


@pytest.mark.asyncio
async def test_request_id_present_in_app_error(client):
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 401
    assert "request_id" in r.json() or "X-Request-Id" in r.headers


@pytest.mark.asyncio
async def test_unknown_route_uses_envelope(client):
    """A route-not-found 404 is framework-raised (not an AppError); it must still
    conform to the envelope and carry a friendly code."""
    r = await client.get("/api/this-route-does-not-exist")
    assert r.status_code == 404
    assert _is_envelope(r.json())
    assert r.json()["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_route_404_captured_when_allowlisted(client, db, monkeypatch):
    """A framework 404 reaches the error-capture path once 4xx capture + the
    allowlist opt it in (previously it bypassed the handlers entirely)."""
    from app.services import error_log, job_queue
    from app.services import settings as ssvc

    k = ssvc.Keys
    ssvc.set_value(db, key=k.ERROR_LOG_CAPTURE_4XX, value="true", actor=None)
    ssvc.set_value(db, key=k.ERROR_LOG_4XX_CODES, value="404", actor=None)
    db.commit()
    error_log._reset_cache()

    calls: list = []
    monkeypatch.setattr(job_queue, "enqueue", lambda name, **kw: calls.append((name, kw)))

    r = await client.get("/api/definitely-not-a-real-endpoint")
    assert r.status_code == 404
    assert any(
        name == "notify_admin_error"
        and kw["event"]["status_code"] == 404
        and kw["event"]["code"] == "NOT_FOUND"
        for name, kw in calls
    )
    error_log._reset_cache()
