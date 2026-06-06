"""SelectiveGZipMiddleware: gzip normal responses, never file downloads.

Gzipping a download response is pointless (binaries don't compress) and
catastrophic in practice - it defeats FileResponse's zero-copy sendfile and
crawls on multi-GB files. Download endpoints all end in /download.
"""
from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware.gzip import SelectiveGZipMiddleware

_BIG = "x" * 5000  # well over minimum_size so gzip would normally engage


async def _json(_request):
    return JSONResponse({"blob": _BIG})


async def _download(_request):
    return PlainTextResponse(_BIG)


def _client() -> TestClient:
    app = Starlette(
        routes=[
            Route("/api/data", _json),
            Route("/api/files/abc/download", _download),
        ]
    )
    app.add_middleware(SelectiveGZipMiddleware, minimum_size=1024)
    return TestClient(app)


def test_json_response_is_gzipped() -> None:
    resp = _client().get("/api/data", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"


def test_download_response_is_not_gzipped() -> None:
    resp = _client().get("/api/files/abc/download", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") != "gzip"
