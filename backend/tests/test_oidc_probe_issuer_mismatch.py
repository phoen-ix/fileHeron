"""The SSO test-connection probe fails for an issuer that sign-in would refuse.

`_probe_issuer` reported `ok` whenever the discovery document LOADED and only
echoed its `issuer` field. Sign-in (services/oidc.py::_discovery) requires that
field to equal the configured issuer, one trailing slash tolerated - so a
provider saved with e.g. Keycloak's legacy `/auth/realms/x` path, whose
discovery answers with the canonical issuer, tested green and then refused
every login with OIDC_ISSUER_MISMATCH.
"""
from __future__ import annotations

import httpx
import pytest

from app.models.user import UserRole
from app.routers.admin import oidc as oidc_router
from tests._http_mock import serve

PW = "Pass12345678!"


def _serve_discovery(monkeypatch, doc):
    # No DNS in the test container, and the probe resolves the host before it
    # connects: stub the SSRF guard, and answer the real streamed fetch with a
    # MockTransport.
    monkeypatch.setattr(oidc_router, "assert_public_http_url", lambda *a, **k: None)
    serve(monkeypatch, lambda _req: httpx.Response(200, json=doc))


async def _admin_headers(make_user, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    token, _ = await login_as("admin@test.local", PW)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_probe_refuses_an_issuer_the_login_path_would_refuse(
    make_user, client, login_as, monkeypatch
):
    h = await _admin_headers(make_user, login_as)
    _serve_discovery(
        monkeypatch,
        {
            "issuer": "https://idp.example.com/realms/fh",
            "authorization_endpoint": "https://idp.example.com/realms/fh/auth",
            "token_endpoint": "https://idp.example.com/realms/fh/token",
        },
    )

    r = await client.post(
        "/api/admin/settings/sso/test-discovery",
        json={"issuer_url": "https://idp.example.com/auth/realms/fh"},
        headers=h,
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert "OIDC_ISSUER_MISMATCH" in body["error"]
    assert "https://idp.example.com/realms/fh" in body["error"]
    # The endpoints are still reported, so the admin sees what the IdP said.
    assert body["issuer"] == "https://idp.example.com/realms/fh"
    assert body["token_endpoint"].endswith("/token")


@pytest.mark.asyncio
async def test_probe_tolerates_exactly_one_trailing_slash(make_user, client, login_as, monkeypatch):
    """The same tolerance the login path applies - the Authentik preset's
    canonical issuer ends in `/`."""
    h = await _admin_headers(make_user, login_as)
    _serve_discovery(monkeypatch, {"issuer": "https://idp.example.com/application/o/fh/"})

    r = await client.post(
        "/api/admin/settings/sso/test-discovery",
        json={"issuer_url": "https://idp.example.com/application/o/fh"},
        headers=h,
    )

    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
