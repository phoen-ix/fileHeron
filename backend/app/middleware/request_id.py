"""RequestIdMiddleware: ensures every request has a request_id available on
`request.state.request_id` and echoed back in the X-Request-Id response header.

If the inbound request already includes X-Request-Id (e.g., from Traefik), we
honor it; otherwise we generate a fresh UUID4.

Implemented as **pure ASGI** (not Starlette's BaseHTTPMiddleware): the latter
pipes the response through an anyio stream wrapper, which defeats
``FileResponse``'s zero-copy ``os.sendfile`` and throttles large downloads to a
crawl. A pure-ASGI middleware only wraps ``send`` to inject the header on the
response-start message and leaves the body path (incl. sendfile) untouched.
"""
from __future__ import annotations

import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_HEADER = "X-Request-Id"
_HEADER_LOWER = b"x-request-id"


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        incoming: str | None = None
        for key, value in scope.get("headers", []):
            if key == _HEADER_LOWER:
                incoming = value.decode("latin-1")
                break
        request_id = incoming if incoming and len(incoming) <= 64 else uuid.uuid4().hex
        # Backed by scope["state"], so request.state.request_id resolves
        # downstream (routers, audit service, error handlers).
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(raw=message.setdefault("headers", []))[_HEADER] = request_id
            await send(message)

        await self._app(scope, receive, send_wrapper)
