"""Error envelope:

    { "error": "Human readable message",
      "code":  "MACHINE_CODE",
      "details": { ... optional ... } }

All 4xx/5xx responses produced by the app conform to this shape so the
frontend never has to switch on error formats.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("fileheron.errors")


class AppError(Exception):
    """Raise this from any handler/service to produce an envelope response."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def _maybe_enqueue_error_alert(
    request: Request, *, status_code: int, code: str, exc: Exception
) -> None:
    """Fire-and-forget: enqueue a server-error alert event for the worker to
    (maybe) email admins. Fully swallowed - this runs on an already-failed
    response and must never add latency or raise a second exception.

    Only server errors (>= 500) qualify; 4xx are expected and never alert. A
    cheap fail-open front guard caps enqueues so a tight 500-loop (e.g. DB down)
    can't flood the job queue - the real cooldown/cap live in the worker."""
    if status_code < 500:
        return
    try:
        from ..services import job_queue, rate_limit
        from ..utils.timeutil import utc_now

        if not rate_limit.check_ip_allowed(
            "err_alert_enqueue", "global", limit=30, window_sec=60
        ):
            return
        event = {
            "source": "http",
            "exception_type": type(exc).__name__,
            "message": (getattr(exc, "message", None) or str(exc))[:500],
            "method": request.method,
            # Path only - never the query string (may carry tokens/PII).
            "path": request.url.path,
            "status_code": status_code,
            "code": code,
            "request_id": getattr(request.state, "request_id", None),
            "user_id": getattr(request.state, "user_id", None),
            "auth_via": getattr(request.state, "auth_via", None),
            "at": utc_now().isoformat(),
        }
        job_queue.enqueue("notify_admin_error", event=event)
    except Exception:
        logger.warning("error-alert enqueue skipped", exc_info=True)


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    body: dict[str, Any] = {"error": exc.message, "code": exc.code}
    if exc.details:
        body["details"] = exc.details
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["request_id"] = request_id
    _maybe_enqueue_error_alert(
        request, status_code=exc.status_code, code=exc.code, exc=exc
    )
    return JSONResponse(status_code=exc.status_code, content=body)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.exception("unhandled exception", extra={"request_id": request_id})
    _maybe_enqueue_error_alert(
        request, status_code=500, code="INTERNAL_ERROR", exc=exc
    )
    body: dict[str, Any] = {"error": "Internal server error.", "code": "INTERNAL_ERROR"}
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(status_code=500, content=body)
