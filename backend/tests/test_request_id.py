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
async def test_an_inbound_request_id_does_not_become_ours(client):
    """It used to be adopted verbatim - and it is the value persisted in
    audit_log and error_log, so a caller could choose the correlation key their
    own rows are filed under: an incident responder filtering on it got an
    arbitrary set of unrelated events, and distinct requests collapsed into one
    (audit #2). Their value is echoed back for their own tracing, under a
    header of its own."""
    inbound = "abcdef0123456789abcdef0123456789"
    r = await client.get("/api/health", headers={"X-Request-Id": inbound})
    assert r.headers.get("X-Request-Id") != inbound
    assert r.headers.get("X-Request-Id")
    assert r.headers.get("X-Client-Request-Id") == inbound


@pytest.mark.asyncio
async def test_inbound_request_id_too_long_is_replaced(client):
    too_long = "x" * 80
    r = await client.get("/api/health", headers={"X-Request-Id": too_long})
    rid = r.headers.get("X-Request-Id")
    assert rid is not None
    assert rid != too_long
