"""Admin CSV exports are bearer-gated.

This is the deterministic proof behind the v1.18.1 fix: the audit/mail/analytics
CSV exports used to be triggered by a plain `<a href download>`, which carries
no Authorization header (the access token lives only in memory, attached by the
axios interceptor) and no useful cookie (the refresh cookie is path-scoped to
/api/auth). So an unauthenticated GET - exactly what the browser anchor sent -
401s. The frontend now fetches these through axios (responseType blob); these
tests guard that the endpoints stay admin-gated.
"""
from __future__ import annotations

import pytest

from app.models.user import UserRole

_EXPORTS = [
    "/api/admin/audit-log/export.csv",
    "/api/admin/mail-log/export.csv",
    "/api/admin/analytics/export.csv",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _EXPORTS)
async def test_csv_export_requires_auth(client, path):
    # No Authorization header - what the old `<a href download>` actually sent.
    resp = await client.get(path)
    assert resp.status_code == 401, f"{path} should require auth, got {resp.status_code}"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _EXPORTS)
async def test_csv_export_forbidden_for_non_admin(make_user, client, login_as, path):
    make_user(email="client@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("client@test.local", "Pass12345678!")
    resp = await client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, f"{path} should be admin-only, got {resp.status_code}"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _EXPORTS)
async def test_csv_export_ok_for_admin(make_user, client, login_as, path):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
