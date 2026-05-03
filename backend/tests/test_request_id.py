"""X-Request-Id middleware: generates UUID per request, echoes inbound when
the client supplies one (≤64 chars), surfaces on every response."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_response_has_request_id(client):
    r = await client.get("/api/health")
    rid = r.headers.get("X-Request-Id")
    assert rid is not None and len(rid) > 0


@pytest.mark.asyncio
async def test_inbound_request_id_is_echoed(client):
    inbound = "abcdef0123456789abcdef0123456789"
    r = await client.get("/api/health", headers={"X-Request-Id": inbound})
    assert r.headers.get("X-Request-Id") == inbound


@pytest.mark.asyncio
async def test_inbound_request_id_too_long_is_replaced(client):
    too_long = "x" * 80
    r = await client.get("/api/health", headers={"X-Request-Id": too_long})
    rid = r.headers.get("X-Request-Id")
    assert rid is not None
    assert rid != too_long
