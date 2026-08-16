"""Scan-guard middleware: classify 4xx on the way out, refuse blocked sources on
the way in.

Pure ASGI, like its three neighbours - never `BaseHTTPMiddleware`, which buffers
and defeats `FileResponse`'s sendfile (the reason is documented in
`security_headers.py` and `request_id.py`).

**Placement is load-bearing.** Registered so the runtime order is
`SecurityHeaders -> RequestId -> ScanGuard -> GZip -> app`:

- *Inside* `RequestId` and `SecurityHeaders`, so the refusal response picks up the
  request id and all five security headers **on the way back out**. That is what
  makes it byte-identical to a genuine 404 without this module hand-stamping
  anything, and without drifting the day the CSP changes.
- *Outside* Starlette's `ExceptionMiddleware`, so a short-circuited response never
  reaches the exception handlers. That is what makes the feedback loop
  structurally impossible rather than merely suppressed: a blocked scanner that
  keeps hammering produces **no** `error_log` rows and **no** ARQ jobs, so
  blocking quiets the log instead of flooding it.

**The refusal is indistinguishable from a real miss.** Same envelope, same
`code`, same headers, same content type. No `X-Blocked-By`, no `Retry-After`,
ever - anything that differs is an oracle telling a scanner which of its proxies
are burned and letting it binary-search the threshold. The one residual
distinguisher is timing (no routing work happens); it is sub-millisecond and not
worth defending, so do not "fix" it with a sleep.

**Counting is synchronous, and that is a deliberate non-fix.** `note_offence`
does sync Redis I/O (and, at a threshold crossing, a DB write) from the response
path, and `redis_client` sets `socket_timeout=2`, so a Redis *slowdown* can hold
the event loop. That is real - but it is not specific to this module: every
per-IP limiter in the product calls the same sync `rate_limit.check_ip_allowed`
from an `async def` handler (`routers/auth.py`, `routers/notifications.py`,
`routers/telemetry.py`). Moving only the guard off-loop was tried and reverted:
`asyncio.to_thread` puts the guard's own `SessionLocal` on a second thread, which
is fine against MariaDB's connection pool and corrupt against the test harness's
single shared SQLite connection (measured: `sqlite3.InterfaceError` writing the
audit row). Whether to take the whole application's Redis calls off the loop is
one decision to make deliberately, not a change to smuggle in here.

**Nothing here may raise.** Both halves are wrapped and default to serving the
request.
"""
from __future__ import annotations

import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..services import scan_guard as guard_svc
from ..utils.client_ip import client_ip_from_scope

logger = logging.getLogger("fileheron.middleware.scan_guard")


class ScanGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        ip = client_ip_from_scope(scope)

        # --- inbound: is this source currently blocked? ---------------------
        try:
            blocked = guard_svc.is_blocked(ip)
        except Exception:
            logger.warning("scan_guard: block check failed; serving", exc_info=True)
            blocked = False
        if blocked:
            state = scope.get("state") or {}
            body = {"error": "Not Found", "code": "NOT_FOUND"}
            request_id = state.get("request_id")
            if request_id:
                body["request_id"] = request_id
            await JSONResponse(body, status_code=404)(scope, receive, send)
            return

        # --- outbound: does this response earn an offence? ------------------
        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                try:
                    self._note(scope, int(message.get("status", 0)), ip)
                except Exception:
                    logger.warning("scan_guard: classify failed", exc_info=True)
            await send(message)

        await self._app(scope, receive, send_wrapper)

    def _note(self, scope: Scope, status: int, ip: str | None) -> None:
        if not ip or not (400 <= status < 500):
            return
        snap = guard_svc.snapshot()
        if not snap.get("enabled"):
            return

        # `request.state` is backed by `scope["state"]`, so values set by the
        # auth dependencies (`dependencies.get_current_user` / `get_actor`) are
        # visible out here without rebuilding a Request.
        state = scope.get("state") or {}
        authenticated = state.get("user_id") is not None

        from .errors import _redact_path

        # Redacted, always. Otherwise a mistyped `/d/<token>` would write a live
        # public-link token into a Redis key and an admin-browsable table - and
        # token-shaped 404s would each look like a distinct path, letting a mail
        # gateway manufacture the diversity the api_404 signal is gated on.
        path = _redact_path(scope.get("path") or "")

        signal = guard_svc.classify(
            status=status,
            path=path,
            authenticated=authenticated,
            error_code=state.get("error_code"),
            snap=snap,
        )
        if signal is None:
            return
        guard_svc.note_offence(ip, signal, path)
