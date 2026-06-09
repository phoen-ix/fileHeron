"""Anonymous client telemetry.

The SPA reports a client-side 404 (a visit to a page path Vue Router couldn't
match) so it lands in the error log alongside backend / edge 404s - the backend
never sees these otherwise (nginx serves the 200 SPA shell for unknown page
paths). Unauthenticated (the 404 page renders for logged-out visitors), opt-in
(no-op unless an admin turned 4xx capture on), hard per-IP rate-limited, and
fire-and-forget: it must never error the caller. Rows are `source="spa"` and, like
any unauthenticated input, client-asserted (spoofable from the caller's own IP) -
bounded by the opt-in gate + rate limit; logged, never emailed.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from ..services import error_log, job_queue, rate_limit
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.telemetry")

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])


class Page404Request(BaseModel):
    path: str = Field(default="", max_length=512)


@router.post("/page-404", status_code=204)
def report_page_404(body: Page404Request, request: Request) -> Response:
    try:
        # Opt-in: do nothing unless an admin enabled 4xx capture (cheap cached check;
        # DB-free between 60s refreshes, so a flood-when-off costs ~nothing).
        if not error_log.capture_4xx_enabled_cached():
            return Response(status_code=204)
        ip = request.client.host if request.client else ""
        if not rate_limit.check_ip_allowed("client_404", ip, limit=10, window_sec=60):
            return Response(status_code=204)
        # Path only - drop the query string (may carry junk/tokens), then truncate.
        path = (body.path or "").split("?", 1)[0][:512]
        event = {
            "source": "spa",
            "exception_type": "ClientNavigation",
            "message": "Client-side 404 (no matching route)",
            "method": "GET",
            "path": path,
            "status_code": 404,
            "code": "NOT_FOUND",
            "ip": ip or None,
            "request_id": getattr(request.state, "request_id", None),
            "user_id": None,
            "auth_via": None,
            "at": utc_now().isoformat(),
        }
        job_queue.enqueue("notify_admin_error", event=event)
    except Exception:
        logger.warning("page-404 beacon skipped", exc_info=True)
    return Response(status_code=204)
