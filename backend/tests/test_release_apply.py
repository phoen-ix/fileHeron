"""Phase 4 self-update: backend → updater HMAC bridge + admin endpoints.

The updater runs in its own container with /var/run/docker.sock; tests
mock the httpx layer so we never actually need it running. We focus on:

- HMAC headers are computed correctly + sent
- Password re-prompt is enforced (wrong password → 401)
- audit_log row is written with the right event type + target_id
- ops_alert dispatched to every non-disabled admin
- error envelopes propagate from updater 4xx/5xx
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

import httpx
import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole
from app.services import release_apply

os.environ.setdefault("UPDATER_HOOK_SECRET", "test-secret-32-bytes-of-entropy-xxxxxxxxxxxx")


# ---------------------------------------------------------------------------
# httpx stubs — mirror Phase 3 release_check pattern.
# ---------------------------------------------------------------------------


class _StubResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("stub", request=None, response=None)  # type: ignore[arg-type]

    def json(self):
        return self._payload


class _StubClient:
    def __init__(self, response_map: dict[tuple[str, str], _StubResponse] | _StubResponse | Exception):
        self._map = response_map
        self.last_post: dict[str, Any] | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    def _resolve(self, method: str, url: str) -> _StubResponse:
        if isinstance(self._map, _StubResponse):
            return self._map
        if isinstance(self._map, Exception):
            raise self._map
        for (m, suffix), resp in self._map.items():
            if m == method and url.endswith(suffix):
                return resp
        raise AssertionError(f"unmatched stub for {method} {url}")

    async def get(self, url, **kw):
        return self._resolve("GET", url)

    async def post(self, url, content=None, headers=None, **kw):
        self.last_post = {"url": url, "content": content, "headers": headers}
        return self._resolve("POST", url)


@pytest.mark.asyncio
async def test_apply_signs_with_hmac(monkeypatch):
    stub = _StubClient(
        _StubResponse({"job_id": "abc", "action": "update", "target_tag": "v0.3.0"})
    )
    monkeypatch.setattr(release_apply.httpx, "AsyncClient", lambda **_kw: stub)

    result = await release_apply.apply(action="update", target_tag="v0.3.0")
    assert result["job_id"] == "abc"
    assert stub.last_post is not None
    body = stub.last_post["content"]
    sig = stub.last_post["headers"]["X-Updater-Sig"]
    expected = hmac.new(
        release_apply._secret().encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    assert sig == expected


@pytest.mark.asyncio
async def test_apply_translates_409_in_progress(monkeypatch):
    stub = _StubClient(
        _StubResponse(
            {"detail": {"code": "UPDATE_IN_PROGRESS", "job_id": "xyz"}},
            status_code=409,
        )
    )
    monkeypatch.setattr(release_apply.httpx, "AsyncClient", lambda **_kw: stub)

    from app.middleware.errors import AppError
    with pytest.raises(AppError) as exc:
        await release_apply.apply(action="update", target_tag="v0.3.0")
    assert exc.value.status_code == 409
    assert exc.value.code == "UPDATE_IN_PROGRESS"


@pytest.mark.asyncio
async def test_update_endpoint_requires_password(client, db, make_user, login_as):
    admin = make_user(email="adm@test.local", role=UserRole.admin)
    token, cookies = await login_as("adm@test.local", "TestPassword123!")
    headers = {"Authorization": f"Bearer {token}"}

    # Wrong password → 401, no updater call.
    r = await client.post(
        "/api/admin/system/update",
        json={"password": "wrong-pw", "target_tag": "v0.3.0"},
        headers=headers,
        cookies=cookies,
    )
    assert r.status_code == 401, r.text
    assert r.json()["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_update_endpoint_writes_audit_and_dispatches(
    client, db, make_user, login_as, monkeypatch
):
    admin = make_user(email="adm2@test.local", role=UserRole.admin)
    # Second admin so the notify-all-admins fan-out actually has someone
    # other than the actor to dispatch to.
    other_admin = make_user(email="adm3@test.local", role=UserRole.admin)
    token, cookies = await login_as("adm2@test.local", "TestPassword123!")
    headers = {"Authorization": f"Bearer {token}"}

    stub = _StubClient(
        _StubResponse({"job_id": "job-abc", "action": "update", "target_tag": "v0.3.0"})
    )
    monkeypatch.setattr(release_apply.httpx, "AsyncClient", lambda **_kw: stub)

    r = await client.post(
        "/api/admin/system/update",
        json={"password": "TestPassword123!", "target_tag": "v0.3.0"},
        headers=headers,
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    assert r.json()["job_id"] == "job-abc"

    # Audit row exists with right type + actor + target.
    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.update_triggered.value)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert row is not None
    assert row.actor_user_id == admin.id
    assert row.target_id == "job-abc"
    assert row.extra and row.extra.get("target_tag") == "v0.3.0"

    # ops_alert fan-out wrote a notification row for the OTHER admin.
    from app.models.notification import Notification, NotificationCategory
    notifs = (
        db.query(Notification)
        .filter(
            Notification.user_id == other_admin.id,
            Notification.category == NotificationCategory.ops_alert,
        )
        .all()
    )
    assert len(notifs) >= 1


@pytest.mark.asyncio
async def test_rollback_endpoint_uses_updater_target(
    client, db, make_user, login_as, monkeypatch
):
    admin = make_user(email="adm4@test.local", role=UserRole.admin)
    token, cookies = await login_as("adm4@test.local", "TestPassword123!")
    headers = {"Authorization": f"Bearer {token}"}

    stub = _StubClient(
        _StubResponse({"job_id": "job-rb", "action": "rollback", "target_tag": "v0.1.0"})
    )
    monkeypatch.setattr(release_apply.httpx, "AsyncClient", lambda **_kw: stub)

    r = await client.post(
        "/api/admin/system/rollback",
        json={"password": "TestPassword123!"},
        headers=headers,
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    assert r.json()["target_tag"] == "v0.1.0"

    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.rollback_triggered.value)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert row is not None
    assert row.actor_user_id == admin.id
    assert row.target_id == "job-rb"
