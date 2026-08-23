"""SelectiveGZipMiddleware: gzip normal responses, never file downloads.

Gzipping a download response is pointless (binaries don't compress) and
catastrophic in practice - it defeats FileResponse's zero-copy sendfile and
crawls on multi-GB files. Download endpoints all end in /download, /download-zip
or /preview.

Driven with httpx's ASGI transport rather than `starlette.testclient.TestClient`:
Starlette deprecated running its sync TestClient on httpx 1.x (it wants httpx2),
which surfaced as a warning the moment the image moved to Python 3.14 and
resolved a newer Starlette. The rest of this suite already talks to the app
asynchronously; this file was the last sync holdout.
"""
from __future__ import annotations

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import (
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from starlette.routing import Route

from app.middleware.gzip import SelectiveGZipMiddleware

_BIG = "x" * 5000  # well over minimum_size so gzip would normally engage


async def _json(_request):
    return JSONResponse({"blob": _BIG})


async def _download(_request):
    return PlainTextResponse(_BIG)


async def _sse(_request):
    """A real streaming SSE response: many small frames, no Content-Length."""
    async def _frames():
        for i in range(200):
            yield f"id: {i}\ndata: {{\"n\": {i}}}\n\n".encode()

    return StreamingResponse(_frames(), media_type="text/event-stream")


def _app() -> Starlette:
    app = Starlette(
        routes=[
            Route("/api/data", _json),
            Route("/api/files/abc/download", _download),
            Route("/api/files/abc/download-zip", _download),
            Route("/api/files/abc/preview", _download),
            Route("/api/notifications/stream", _sse),
            Route("/api/admin/system/stream", _sse),
        ]
    )
    app.add_middleware(SelectiveGZipMiddleware, minimum_size=1024)
    return app


async def _get(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        return await client.get(path, headers={"Accept-Encoding": "gzip"})


@pytest.mark.asyncio
async def test_json_response_is_gzipped() -> None:
    resp = await _get("/api/data")
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"


@pytest.mark.parametrize(
    "path",
    [
        "/api/files/abc/download",
        "/api/files/abc/download-zip",
        "/api/files/abc/preview",
    ],
)
@pytest.mark.asyncio
async def test_byte_serving_responses_are_not_gzipped(path: str) -> None:
    """All three suffixes the middleware exempts. `/preview` serves the same raw
    bytes as `/download` and was missing from the list once - on the anonymous
    public-link route that made gzip level 9 on the event loop reachable without
    authentication (audit 2026-07-30)."""
    resp = await _get(path)
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") != "gzip"


@pytest.mark.parametrize(
    "path", ["/api/notifications/stream", "/api/admin/system/stream"]
)
@pytest.mark.asyncio
async def test_sse_streams_are_not_gzipped(path: str) -> None:
    """Compressing an event stream is how a live feed silently stops arriving:
    a deflate window accumulates small frames and emits nothing until it fills.

    Starlette 1.3.1 already excludes `text/event-stream` by content type, so
    this was not broken - but that is a DEPENDENCY DEFAULT, free to change on a
    bump, and this middleware exists precisely because the framework's own gzip
    policy is not the one this app wants. The routes' `X-Accel-Buffering: no`
    and `Cache-Control: no-transform` govern nginx and Traefik, not the ASGI
    stack in-process.
    """
    resp = await _get(path)
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") is None
    assert resp.headers["content-type"].startswith("text/event-stream")


@pytest.mark.asyncio
async def test_the_exemption_is_ours_and_not_the_frameworks() -> None:
    """Pin the rule in THIS module, so a Starlette bump that drops its
    content-type exclusion cannot quietly start compressing the bell."""
    from app.middleware.gzip import _is_download

    assert _is_download({"type": "http", "path": "/api/notifications/stream"})
    assert _is_download({"type": "http", "path": "/api/admin/system/stream"})
    assert not _is_download({"type": "http", "path": "/api/notifications"})
