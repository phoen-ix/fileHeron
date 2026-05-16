"""Phase 3 self-update: GitHub releases polling caches into app_settings,
the admin /system/status endpoint reads it, and update_available flips
when the cached `latest_version` differs from the running VERSION."""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.services import release_check as rc
from app.services import settings as settings_svc


class _StubResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "stub", request=None, response=None  # type: ignore[arg-type]
            )

    def json(self):
        return self._payload


class _StubClient:
    def __init__(self, response: _StubResponse | Exception):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def get(self, *_a, **_kw):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


@pytest.mark.asyncio
async def test_release_check_caches_on_success(db, monkeypatch):
    payload = {
        "tag_name": "v0.2.0",
        "name": "Server v0.2.0",
        "published_at": "2026-05-16T10:00:00Z",
        "body": "## Changes\n- something",
        "html_url": "https://github.com/phoen-ix/fileHeron/releases/tag/v0.2.0",
    }
    monkeypatch.setattr(
        rc.httpx, "AsyncClient", lambda **_kw: _StubClient(_StubResponse(payload))
    )
    result = await rc.release_check.__wrapped__(None)  # bypass @track_cron for unit
    assert result["ok"] is True
    assert result["latest_version"] == "v0.2.0"

    cached = rc.read_cached(db)
    assert cached["latest_version"] == "v0.2.0"
    assert cached["latest_body"] == "## Changes\n- something"
    assert cached["latest_url"].endswith("/v0.2.0")
    assert cached["last_check_at"]  # ISO timestamp
    assert cached["last_check_error"] is None


@pytest.mark.asyncio
async def test_release_check_records_upstream_failure(db, monkeypatch):
    monkeypatch.setattr(
        rc.httpx,
        "AsyncClient",
        lambda **_kw: _StubClient(httpx.ConnectError("network down")),
    )
    result = await rc.release_check.__wrapped__(None)
    assert result["ok"] is False
    assert "network down" in result["error"]

    cached = rc.read_cached(db)
    # No version overwrite on failure — last successful version stays.
    # (here there was no prior version, so it remains None.)
    assert cached["latest_version"] is None
    assert cached["last_check_error"]
    assert cached["last_check_at"]  # always set, even on failure


@pytest.mark.asyncio
async def test_system_status_flags_update_available(
    client, db, make_user, login_as, monkeypatch
):
    admin = make_user(email="admin@test.local", role="admin")  # type: ignore[arg-type]
    # The role enum needs UserRole.admin; the factory accepts the enum but
    # also a string via Pydantic's strict mode. Use the enum to be safe.
    from app.models.user import UserRole
    admin.role = UserRole.admin
    db.add(admin)
    db.commit()

    token, cookies = await login_as("admin@test.local", "TestPassword123!")
    headers = {"Authorization": f"Bearer {token}"}

    # No cache yet → update_available should be False whatever the running
    # version is (the test runner's image bakes its own VERSION).
    r = await client.get("/api/admin/system/status", headers=headers, cookies=cookies)
    assert r.status_code == 200, r.text
    v = r.json()["version"]
    running = v["running"]
    assert running  # any non-empty value the image was built with
    assert v["latest"] is None
    assert v["update_available"] is False

    # Prime the cache with a release that differs from running, re-hit.
    fake_latest = f"v999.{running}"
    settings_svc.set_value(
        db, key=rc.CacheKeys.LATEST_VERSION, value=fake_latest, actor=None
    )
    settings_svc.set_value(
        db, key=rc.CacheKeys.LAST_CHECK_AT, value="2026-05-16T10:00:00", actor=None
    )
    db.commit()

    r = await client.get("/api/admin/system/status", headers=headers, cookies=cookies)
    v = r.json()["version"]
    assert v["latest"] == fake_latest
    assert v["update_available"] is True
