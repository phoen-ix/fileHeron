"""PUT /api/admin/settings/updates applies the release check's address policy
when the URL is SAVED, not only when it is used.

It checked only for an http(s) prefix, so a loopback or cloud-metadata URL was
stored with "Saved" and refused on the next release check (audit 2026-11-08).
A host that does not resolve at save time is kept: it is re-checked at use, and
an internal mirror may simply be unreachable from where the admin saves.
"""
from __future__ import annotations

import socket

import pytest

from app.models.user import UserRole
from app.services import settings as settings_svc

PW = "Pass12345678!"


async def _put(make_user, login_as, client, url: str):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    token, _ = await login_as("admin@test.local", PW)
    return await client.put(
        "/api/admin/settings/updates",
        json={"api_url": url},
        headers={"Authorization": f"Bearer {token}"},
    )


def _resolve_to(monkeypatch, addr: str | None):
    def _fake(host, port, *a, **kw):
        if addr is None:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port or 443))]

    monkeypatch.setattr("app.utils.net.socket.getaddrinfo", _fake)


@pytest.mark.asyncio
@pytest.mark.parametrize("addr", ["127.0.0.1", "169.254.169.254"])
async def test_a_non_routable_target_is_refused_at_save(make_user, login_as, client, db, monkeypatch, addr):
    _resolve_to(monkeypatch, addr)
    r = await _put(make_user, login_as, client, "http://mirror.internal/releases")
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "URL_BLOCKED"
    assert settings_svc.get(db, settings_svc.Keys.UPDATES_API_URL) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("addr", ["10.0.0.5", None])
async def test_a_private_mirror_or_an_unresolvable_host_is_kept(make_user, login_as, client, db, monkeypatch, addr):
    _resolve_to(monkeypatch, addr)
    r = await _put(make_user, login_as, client, "https://mirror.internal/releases")
    assert r.status_code == 200, r.text
    assert settings_svc.get(db, settings_svc.Keys.UPDATES_API_URL) == "https://mirror.internal/releases"
