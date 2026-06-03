"""Security headers must be present on every response."""
from __future__ import annotations

import pytest

_REQUIRED = {
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Permissions-Policy",
    "Content-Security-Policy",
}


@pytest.mark.asyncio
async def test_headers_on_health(client):
    r = await client.get("/api/health")
    for h in _REQUIRED:
        assert h in r.headers, f"missing {h} in /api/health"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_headers_on_error_response(client):
    """Error responses (e.g. 401) must also carry security headers."""
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 401
    for h in _REQUIRED:
        assert h in r.headers, f"missing {h} on error response"
