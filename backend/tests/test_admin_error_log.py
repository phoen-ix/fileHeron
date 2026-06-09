"""/api/admin/error-log - list + filter + detail + CSV (admin-gated)."""
from __future__ import annotations

import pytest

from app.models.user import UserRole
from app.services import error_log
from app.utils.timeutil import utc_now

_PW = "Pass12345678!"


def _seed(db):
    error_log.record(
        db,
        {
            "source": "http", "status_code": 500, "code": "INTERNAL_ERROR",
            "method": "GET", "path": "/api/x", "message": "boom", "at": utc_now(),
        },
        signature="s500",
    )
    error_log.record(
        db,
        {
            "source": "http", "status_code": 429, "code": "RATE_LIMITED",
            "method": "POST", "path": "/api/y", "message": "slow down", "at": utc_now(),
        },
        signature="s429",
    )
    db.commit()


async def _admin_token(make_user, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password=_PW)
    token, _ = await login_as("admin@test.local", _PW)
    return token


def _h(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_list_and_filter(make_user, db, client, login_as):
    token = await _admin_token(make_user, login_as)
    _seed(db)
    r = await client.get("/api/admin/error-log", headers=_h(token))
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2

    r = await client.get("/api/admin/error-log?code=RATE_LIMITED", headers=_h(token))
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["status_code"] == 429

    r = await client.get("/api/admin/error-log?status_code=500", headers=_h(token))
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["code"] == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_detail_and_404(make_user, db, client, login_as):
    token = await _admin_token(make_user, login_as)
    _seed(db)
    rows, _ = error_log.list_errors(db)
    rid = rows[0].id
    r = await client.get(f"/api/admin/error-log/{rid}", headers=_h(token))
    assert r.status_code == 200
    assert r.json()["id"] == rid

    r = await client.get("/api/admin/error-log/99999999", headers=_h(token))
    assert r.status_code == 404
    assert r.json()["code"] == "ERROR_LOG_NOT_FOUND"


@pytest.mark.asyncio
async def test_csv_export(make_user, db, client, login_as):
    token = await _admin_token(make_user, login_as)
    _seed(db)
    r = await client.get("/api/admin/error-log/export.csv", headers=_h(token))
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "INTERNAL_ERROR" in r.text
    assert "RATE_LIMITED" in r.text


@pytest.mark.asyncio
async def test_non_admin_forbidden(make_user, client, login_as):
    make_user(email="c@test.local", role=UserRole.client, password=_PW)
    token, _ = await login_as("c@test.local", _PW)
    r = await client.get("/api/admin/error-log", headers=_h(token))
    assert r.status_code == 403
