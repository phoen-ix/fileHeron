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
async def test_url_override_is_used(db, monkeypatch):
    """When the admin sets `updates.api_url`, the cron + on-demand both
    GET that URL instead of the default upstream."""
    captured: dict[str, str] = {}

    class _CapturingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, **_kw):
            captured["url"] = url
            return _StubResponse(
                {
                    "tag_name": "v9.9.9",
                    "html_url": "https://example.com/r",
                    "body": "x",
                    "published_at": "2026-05-16T10:00:00Z",
                }
            )

    monkeypatch.setattr(rc.httpx, "AsyncClient", lambda **_kw: _CapturingClient())
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.UPDATES_API_URL,
        value="https://example.com/fork/releases/latest",
        actor=None,
    )
    db.commit()

    await rc.run_check(db, manual=True)
    assert captured["url"] == "https://example.com/fork/releases/latest"


@pytest.mark.asyncio
async def test_manual_mode_skips_cron_work(db, monkeypatch):
    """When check_mode=manual, the cron returns without HTTP."""
    called = {"n": 0}

    class _ShouldNeverGet:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, *_a, **_kw):
            called["n"] += 1
            return _StubResponse({"tag_name": "v0.0.0"})

    monkeypatch.setattr(rc.httpx, "AsyncClient", lambda **_kw: _ShouldNeverGet())
    settings_svc.set_value(
        db, key=settings_svc.Keys.UPDATES_CHECK_MODE, value="manual", actor=None
    )
    db.commit()

    result = await rc.run_check(db, manual=False)
    assert result == {"ok": True, "skipped": "manual_mode"}
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_24h_guard_skips_recent_success(db, monkeypatch):
    """When the last SUCCESSFUL check was less than 24h ago, the cron
    short-circuits without an HTTP call. Reads LAST_SUCCESS_AT, not
    LAST_CHECK_AT, so failures don't block retries."""
    called = {"n": 0}

    class _ShouldNeverGet:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, *_a, **_kw):
            called["n"] += 1
            return _StubResponse({"tag_name": "v0.0.0"})

    monkeypatch.setattr(rc.httpx, "AsyncClient", lambda **_kw: _ShouldNeverGet())
    from datetime import datetime, timedelta
    one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    settings_svc.set_value(
        db, key=rc.CacheKeys.LAST_SUCCESS_AT, value=one_hour_ago, actor=None
    )
    db.commit()

    result = await rc.run_check(db, manual=False)
    assert result["ok"] is True
    assert result["skipped"] == "too_soon"
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_failure_does_not_advance_success_timer(db, monkeypatch):
    """A failed check writes last_check_at but NOT last_success_at, so
    the next hourly tick still tries again instead of waiting 24h."""
    import httpx
    monkeypatch.setattr(
        rc.httpx, "AsyncClient",
        lambda **_kw: _StubClient(httpx.ConnectError("upstream down")),
    )

    # First attempt fails.
    r1 = await rc.run_check(db, manual=False)
    assert r1["ok"] is False
    assert settings_svc.get(db, rc.CacheKeys.LAST_CHECK_AT)  # was set
    assert settings_svc.get(db, rc.CacheKeys.LAST_SUCCESS_AT) is None  # was NOT

    # Second attempt: should still run (not too_soon), still fails.
    r2 = await rc.run_check(db, manual=False)
    assert r2["ok"] is False
    # _too_soon would have returned True if it read last_check_at.


@pytest.mark.asyncio
async def test_success_advances_both_timers(db, monkeypatch):
    """Successful check advances both last_check_at AND last_success_at,
    so subsequent ticks within 24h skip."""
    monkeypatch.setattr(
        rc.httpx, "AsyncClient",
        lambda **_kw: _StubClient(_StubResponse({
            "tag_name": "v9.9.9",
            "html_url": "https://example.com/r",
            "body": "",
            "published_at": "",
        }))
    )

    r1 = await rc.run_check(db, manual=False)
    assert r1["ok"] is True
    assert settings_svc.get(db, rc.CacheKeys.LAST_CHECK_AT)
    assert settings_svc.get(db, rc.CacheKeys.LAST_SUCCESS_AT)

    # Second attempt within the window: skipped.
    r2 = await rc.run_check(db, manual=False)
    assert r2["ok"] is True
    assert r2["skipped"] == "too_soon"


@pytest.mark.asyncio
async def test_manual_run_bypasses_both_guards(db, monkeypatch):
    """`run_check(manual=True)` ignores mode and the 24h guard."""
    called = {"n": 0}

    class _Stub:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, *_a, **_kw):
            called["n"] += 1
            return _StubResponse(
                {
                    "tag_name": "v0.0.1",
                    "html_url": "x",
                    "body": "",
                    "published_at": "",
                }
            )

    monkeypatch.setattr(rc.httpx, "AsyncClient", lambda **_kw: _Stub())
    settings_svc.set_value(
        db, key=settings_svc.Keys.UPDATES_CHECK_MODE, value="manual", actor=None
    )
    from datetime import datetime
    settings_svc.set_value(
        db, key=rc.CacheKeys.LAST_CHECK_AT, value=datetime.utcnow().isoformat(),
        actor=None,
    )
    db.commit()

    result = await rc.run_check(db, manual=True)
    assert result["ok"] is True
    assert result["latest_version"] == "v0.0.1"
    assert called["n"] == 1


@pytest.mark.asyncio
async def test_new_version_dispatches_to_all_admins_once(db, make_user, monkeypatch):
    """First sighting of a new tag → notification rows for every
    non-disabled admin. Second sighting of the SAME tag → zero
    additional rows (dedup via `release.notified_version`)."""
    from app.models.notification import Notification, NotificationCategory
    from app.models.user import UserRole

    a1 = make_user(email="adm1@test.local", role=UserRole.admin)
    a2 = make_user(email="adm2@test.local", role=UserRole.admin)
    _client = make_user(email="cli@test.local")  # not admin → no notification

    monkeypatch.setattr(
        rc.httpx, "AsyncClient",
        lambda **_kw: _StubClient(_StubResponse({
            "tag_name": "v99.99.99",
            "html_url": "https://example.com/r",
            "body": "",
            "published_at": "",
        }))
    )
    r1 = await rc.run_check(db, manual=True)
    assert r1["admins_notified"] == 2

    notifs = (
        db.query(Notification)
        .filter(Notification.category == NotificationCategory.release_available)
        .all()
    )
    assert {n.user_id for n in notifs} == {a1.id, a2.id}

    # Second poll with the same tag → no new notifications.
    r2 = await rc.run_check(db, manual=True)
    assert r2["admins_notified"] == 0
    notifs2 = (
        db.query(Notification)
        .filter(Notification.category == NotificationCategory.release_available)
        .all()
    )
    assert len(notifs2) == 2  # unchanged


@pytest.mark.asyncio
async def test_no_notification_when_target_equals_running(db, make_user, monkeypatch):
    """If the upstream `latest` matches the running VERSION, skip the
    notification fan-out — you don't notify admins about their own
    deployed version."""
    from app.models.notification import Notification, NotificationCategory
    from app.models.user import UserRole
    from app import version as version_mod

    make_user(email="adm@test.local", role=UserRole.admin)

    monkeypatch.setattr(
        rc.httpx, "AsyncClient",
        lambda **_kw: _StubClient(_StubResponse({
            "tag_name": version_mod.VERSION,
            "html_url": "https://example.com/r",
            "body": "",
            "published_at": "",
        }))
    )
    r = await rc.run_check(db, manual=True)
    assert r["admins_notified"] == 0
    n = (
        db.query(Notification)
        .filter(Notification.category == NotificationCategory.release_available)
        .count()
    )
    assert n == 0


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
