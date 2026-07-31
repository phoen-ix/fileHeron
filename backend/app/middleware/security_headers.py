"""SecurityHeadersMiddleware: standard set of headers applied to every response.

CSP is intentionally relaxed for `style-src 'self' 'unsafe-inline'` because the SPA
ships literal `style="..."` attributes and Vue `:style` bindings (the upload
progress bar, the analytics bar fills), and a style attribute falls back to
`style-src` when `style-src-attr` is absent. Element Plus, which this used to be
blamed on, was removed long ago; tightening this means auditing those bindings out
of the templates first, not deleting the directive.

Implemented as **pure ASGI** (not BaseHTTPMiddleware) so it doesn't wrap the
response body - preserving ``FileResponse``'s zero-copy ``os.sendfile`` for
large downloads (BaseHTTPMiddleware throttled them to a crawl).
"""
from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, is_production: bool = False) -> None:
        self._app = app
        self._is_production = is_production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message.setdefault("headers", []))
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                headers["Content-Security-Policy"] = _CSP
                if self._is_production:
                    headers["Strict-Transport-Security"] = (
                        "max-age=31536000; includeSubDomains"
                    )
            await send(message)

        await self._app(scope, receive, send_wrapper)
