"""RequestIdMiddleware: ensures every request has a request_id available
on `request.state.request_id` and echoed back in the X-Request-Id response header.

If the inbound request already includes X-Request-Id (e.g., from Traefik), we
honor it; otherwise we generate a fresh UUID4.
"""
from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIdMiddleware(BaseHTTPMiddleware):
    HEADER = "X-Request-Id"

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(self.HEADER)
        request_id = incoming if incoming and len(incoming) <= 64 else uuid.uuid4().hex
        request.state.request_id = request_id
        response: Response = await call_next(request)
        response.headers[self.HEADER] = request_id
        return response
