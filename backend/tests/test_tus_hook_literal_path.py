"""The tusd hook answers only its literal path (audit 2026-11-19).

uvicorn percent-decodes the path before routing, so `/api/internal%2Ftus-hooks`
reached the hook while a proxy deny rule on PathPrefix(`/api/internal/`) let it
through wherever encoded slashes are allowed - measured on the reference host,
where TUS_HOOK_ALLOWED_IPS was then the only thing in the way. tusd always
sends the literal path; any other spelling is refused like an unknown route.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_the_literal_path_reaches_the_hook(client):
    # `{}` names no hook type, so even a reached hook changes nothing.
    resp = await client.post("/api/internal/tus-hooks?probe=1", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ignored"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/api/internal%2Ftus-hooks", "/api/internal%2ftus-hooks"],
)
async def test_an_encoded_spelling_is_refused_like_a_missing_route(client, path):
    resp = await client.post(path, json={})
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "NOT_FOUND"
