"""SelectiveGZipMiddleware: gzip normal (JSON/text) responses but NEVER file
downloads.

Starlette's GZipMiddleware compresses any response above ``minimum_size`` when
the client sends ``Accept-Encoding: gzip`` (browsers always do). For a file
download that is disastrous: it (a) gzips large, already-incompressible blobs
(e.g. a multi-GB ISO) at a few KB/s, and (b) defeats ``FileResponse``'s
zero-copy ``os.sendfile`` path. Both make large downloads crawl.

File-download endpoints end in ``/download`` (``/api/files/{id}/download``,
``/api/public/{token}/files/{id}/download``,
``/api/admin/files/{id}/quarantine/download``) or ``/download-zip`` (the bulk-ZIP
streams, ``/api/files/{share_id}/download-zip`` + ``/api/public/{token}/download-zip``),
so we bypass gzip for those and delegate everything else to the real
GZipMiddleware. Gzip-ing a streamed ZIP is doubly wrong: it re-compresses an
already-incompressible archive AND strips the Content-Length we computed for the
progress bar. JSON responses — including the ``…/download-zip-url`` mint — keep
compression.
"""
from __future__ import annotations

from fastapi.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


def _is_download(scope: Scope) -> bool:
    return scope["type"] == "http" and scope.get("path", "").endswith(
        ("/download", "/download-zip")
    )


class SelectiveGZipMiddleware:
    def __init__(self, app: ASGIApp, *, minimum_size: int = 1024) -> None:
        self._app = app
        self._gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if _is_download(scope):
            await self._app(scope, receive, send)
        else:
            await self._gzip(scope, receive, send)
