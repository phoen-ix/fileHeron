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
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("fileheron.errors")

# Friendly codes for framework-raised HTTPExceptions (route-not-found 404, 405,
# …). App code uses AppError with its own codes; this only covers the exceptions
# Starlette/FastAPI raise themselves.
_HTTP_STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "AUTH_REQUIRED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    406: "NOT_ACCEPTABLE",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
}

# Known-benign error codes that are never worth capturing in the error log.
# JOB_NOT_FOUND: the self-update UI polls GET /api/admin/system/update-jobs/{id}
# every couple of seconds while the executor is rewriting the job record. The
# record is NOT wiped by the container swap - it lives on the data/updater bind
# mount that backend, shim and executor all share - so a 404 here means the poll
# read the file between a writer's truncate and its write, or a newer job replaced
# it. Expected noise from a poll racing a writer, not an error and not an attack.
# If these ever need chasing, the signal is the backend log's "state file
# unreadable" tracebacks (services/release_apply.py::_read_state), not this table.
_NEVER_CAPTURE_CODES = frozenset({"JOB_NOT_FOUND"})


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


# Two anonymous route families carry a live bearer secret in the PATH itself:
# the public-link token (/api/public/<token>/...) and the 180-day
# manage-notifications token. Anything captured here is persisted to
# `error_log.path`, browsable at /admin/error-log, streamed by the CSV export,
# and rendered into the server_error alert mail - which under
# recipients_mode=custom goes to arbitrary addresses. A raw token would
# therefore outlive the link it opens and hand full download access to whoever
# read the alert (audit 2026-07-30). The query string was already excluded for
# exactly this reason; the path was not.
_SECRET_PATH_PREFIXES = ("/api/public", "/api/notification-subscriptions")


def _redact_path(path: str) -> str:
    """Collapse the token segment of a secret-bearing path to `:token`.

    Keeps the route shape, which is what error triage and the `signature`
    grouping actually need, and drops the credential."""
    for prefix in _SECRET_PATH_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            parts = path.split("/")
            # ["", "api", "public", "<token>", ...] - the 4th element is the secret.
            if len(parts) > 3 and parts[3]:
                parts[3] = ":token"
            return "/".join(parts)
    return path


def _maybe_enqueue_error_event(
    request: Request, *, status_code: int, code: str, exc: Exception
) -> None:
    """Fire-and-forget: enqueue an error event for the worker to LOG (always, when
    logging is on) and maybe alert. Fully swallowed - this runs on an already-
    failed response and must never add latency or raise a second exception.

    5xx always enqueue. 4xx enqueue only when 4xx capture is switched on (a cheap
    cached flag), and behind a tighter front guard so high-volume 4xx noise can't
    starve 5xx or flood the queue; the worker re-checks the allowlist
    authoritatively. The real cooldown/cap (for alerts) live in the worker."""
    if code in _NEVER_CAPTURE_CODES:
        return
    try:
        from ..services import error_log, job_queue, rate_limit
        from ..utils.timeutil import utc_now

        if status_code >= 500:
            bucket, limit = "err_alert_enqueue", 30
        elif 400 <= status_code < 500 and error_log.capture_4xx_enabled_cached():
            # Admin-tunable ceiling (Advanced) so scan capture isn't stuck at a hard
            # default; cached, so no per-error DB read. nginx limit_req sheds the rest.
            bucket, limit = "err_alert_enqueue_4xx", error_log.capture_rate_per_min_cached()
        else:
            return
        if not rate_limit.check_ip_allowed(bucket, "global", limit=limit, window_sec=60):
            return
        event = {
            "source": "http",
            "exception_type": type(exc).__name__,
            "message": (getattr(exc, "message", None) or str(exc))[:500],
            "method": request.method,
            # Path only - never the query string (may carry tokens/PII) - and
            # with any secret-bearing segment collapsed (see _redact_path).
            "path": _redact_path(request.url.path),
            "status_code": status_code,
            "code": code,
            "ip": request.client.host if request.client else None,
            "request_id": getattr(request.state, "request_id", None),
            "user_id": getattr(request.state, "user_id", None),
            "auth_via": getattr(request.state, "auth_via", None),
            "at": utc_now().isoformat(),
        }
        job_queue.enqueue("notify_admin_error", event=event)
    except Exception:
        logger.warning("error-event enqueue skipped", exc_info=True)


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    body: dict[str, Any] = {"error": exc.message, "code": exc.code}
    if exc.details:
        body["details"] = exc.details
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["request_id"] = request_id
    _maybe_enqueue_error_event(
        request, status_code=exc.status_code, code=exc.code, exc=exc
    )
    return JSONResponse(status_code=exc.status_code, content=body)


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Framework-raised HTTPException (route-not-found 404, method-not-allowed 405,
    a library raising HTTPException(503), …). App code raises AppError; this
    normalises Starlette/FastAPI HTTPExceptions to the same envelope AND funnels
    them through the error-capture path - so an allowlisted route-level 404 (which
    never reaches app_error_handler) gets logged like any other error. Pydantic
    422s use a different exception type and keep FastAPI's default {"detail":[…]}."""
    assert isinstance(exc, StarletteHTTPException)
    status_code = exc.status_code
    code = _HTTP_STATUS_CODES.get(status_code, f"HTTP_{status_code}")
    _maybe_enqueue_error_event(request, status_code=status_code, code=code, exc=exc)
    detail = exc.detail
    body: dict[str, Any] = {
        "error": detail if isinstance(detail, str) and detail else "HTTP error",
        "code": code,
    }
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(
        status_code=status_code, content=body, headers=getattr(exc, "headers", None)
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.exception("unhandled exception", extra={"request_id": request_id})
    _maybe_enqueue_error_event(
        request, status_code=500, code="INTERNAL_ERROR", exc=exc
    )
    body: dict[str, Any] = {"error": "Internal server error.", "code": "INTERNAL_ERROR"}
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(status_code=500, content=body)
