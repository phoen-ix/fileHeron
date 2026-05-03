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


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    body: dict[str, Any] = {"error": exc.message, "code": exc.code}
    if exc.details:
        body["details"] = exc.details
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(status_code=exc.status_code, content=body)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.exception("unhandled exception", extra={"request_id": request_id})
    body: dict[str, Any] = {"error": "Internal server error.", "code": "INTERNAL_ERROR"}
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(status_code=500, content=body)
