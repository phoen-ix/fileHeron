"""POST /api/admin/quarantine/av-reload - the AV panel's "Reload signatures".

clamd is faked at the socket, so the reply parsing in
`av_scan.reload_signatures` runs for real. Every outcome leaves an
`av_reload_triggered` audit row: the trail records failed attempts too.
"""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole
from app.services import av_scan

URL = "/api/admin/quarantine/av-reload"
PASSWORD = "Pass12345678!"


class _Clamd:
    """A clamd that answers every command with `reply`."""

    reply = b"RELOADING\0"
    sent: list[bytes] = []

    def __init__(self, *_a, **_kw):
        self._reply = type(self).reply

    def settimeout(self, _t):
        return None

    def connect(self, _addr):
        return None

    def sendall(self, data):
        type(self).sent.append(data)

    def recv(self, _n):
        r, self._reply = self._reply, b""
        return r

    def close(self):
        return None


@pytest.fixture
def clamd(monkeypatch):
    monkeypatch.setattr(av_scan.settings, "AV_SKIP", False)
    monkeypatch.setattr(av_scan.socket, "socket", _Clamd)
    _Clamd.reply = b"RELOADING\0"
    _Clamd.sent = []
    return _Clamd


async def _headers(make_user, login_as, role=UserRole.admin):
    email = f"av-reload-{role.value}@test.local"
    make_user(email=email, role=role, password=PASSWORD)
    token, _ = await login_as(email, PASSWORD)
    return {"Authorization": f"Bearer {token}"}


def _audit_rows(db) -> list[dict]:
    db.expire_all()
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.av_reload_triggered)
        .order_by(AuditLog.id)
        .all()
    )
    return [r.extra for r in rows]


@pytest.mark.asyncio
async def test_reload_tells_clamd_and_audits(client, db, make_user, login_as, clamd):
    h = await _headers(make_user, login_as)
    r = await client.post(URL, headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "av_skip": False, "raw": "RELOADING"}
    assert clamd.sent == [b"zRELOAD\0"]
    assert _audit_rows(db) == [{"ok": True, "av_skip": False, "raw": "RELOADING"}]


@pytest.mark.asyncio
async def test_a_reply_other_than_reloading_is_a_503(client, db, make_user, login_as, clamd):
    clamd.reply = b"UNKNOWN COMMAND\0"
    h = await _headers(make_user, login_as)
    r = await client.post(URL, headers=h)
    assert r.status_code == 503 and r.json()["code"] == "AV_UNAVAILABLE"
    assert _audit_rows(db) == [{"ok": False, "av_skip": False, "raw": "UNKNOWN COMMAND"}]


@pytest.mark.asyncio
async def test_av_skip_is_a_503_and_never_calls_clamd(client, db, make_user, login_as, clamd,
                                                     monkeypatch):
    monkeypatch.setattr(av_scan.settings, "AV_SKIP", True)
    h = await _headers(make_user, login_as)
    r = await client.post(URL, headers=h)
    assert r.status_code == 503 and r.json()["code"] == "AV_UNAVAILABLE"
    assert clamd.sent == []
    assert _audit_rows(db) == [{"ok": False, "av_skip": True, "raw": "AV_SKIP set"}]


@pytest.mark.asyncio
async def test_unreachable_clamd_is_a_503_and_audited(client, db, make_user, login_as,
                                                      monkeypatch):
    monkeypatch.setattr(av_scan.settings, "AV_SKIP", False)

    def _down(*_a, **_kw):
        raise av_scan.AVUnavailableError("cannot connect to clamav:3310: refused")

    monkeypatch.setattr(av_scan, "_open_clamd_socket", _down)
    h = await _headers(make_user, login_as)
    r = await client.post(URL, headers=h)
    assert r.status_code == 503 and r.json()["code"] == "AV_UNAVAILABLE"
    assert "unreachable" in r.json()["error"]
    assert _audit_rows(db) == [
        {"ok": False, "av_skip": False, "error": "cannot connect to clamav:3310: refused"}
    ]


@pytest.mark.asyncio
async def test_reload_is_admin_only(client, db, make_user, login_as, clamd):
    h = await _headers(make_user, login_as, UserRole.employee)
    assert (await client.post(URL, headers=h)).status_code == 403
    assert clamd.sent == []
    assert _audit_rows(db) == []
