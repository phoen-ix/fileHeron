"""Anonymous client telemetry.

Two beacons, both anonymous, both opt-in behind the same 4xx-capture switch,
both hard rate-limited per IP, both fire-and-forget.

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


class CspReport(BaseModel):
    """The `report-uri` payload shape, wrapped in a `csp-report` object.

    Only the fields worth recording are declared; browsers send more and the
    model ignores the rest."""

    document_uri: str = Field(default="", max_length=512, alias="document-uri")
    violated_directive: str = Field(
        default="", max_length=128, alias="violated-directive"
    )
    effective_directive: str = Field(
        default="", max_length=128, alias="effective-directive"
    )
    blocked_uri: str = Field(default="", max_length=512, alias="blocked-uri")
    source_file: str = Field(default="", max_length=512, alias="source-file")
    line_number: int | None = Field(default=None, alias="line-number")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class CspReportEnvelope(BaseModel):
    csp_report: CspReport = Field(default_factory=CspReport, alias="csp-report")

    model_config = {"populate_by_name": True, "extra": "ignore"}


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


@router.post("/csp-report", status_code=204)
async def report_csp_violation(request: Request) -> Response:
    """Sink for `Content-Security-Policy-Report-Only`.

    Shipping a policy in report-only mode and then having nowhere for the
    reports to go means "observe for a release" observes nothing - which on a
    single-tenant self-hosted instance is the whole of the rollout plan. Rows
    land in the error log as `source="csp"` so an admin can see, before the
    policy is enforced, exactly what it would have broken.

    Browsers POST this with `Content-Type: application/csp-report`, so the body
    is read and parsed by hand rather than declared as a JSON model."""
    try:
        # Gated on `error_log.enabled` (default ON), NOT on the 4xx capture flag
        # (default OFF). A CSP report is not a 4xx - the browser is reporting
        # that a policy WOULD have blocked something. Gating it on 4xx capture
        # meant a default instance discarded every report, while the rollout
        # plan for this policy is "enforce once the reports come back empty":
        # empty was the default state, so the criterion was satisfied by a
        # policy that had never been exercised (res-06). The rate limit below is
        # what bounds the volume, not the capture flag.
        if not error_log.log_enabled_cached():
            return Response(status_code=204)
        ip = request.client.host if request.client else ""
        if not rate_limit.check_ip_allowed("csp_report", ip, limit=20, window_sec=60):
            return Response(status_code=204)
        raw = await request.body()
        if len(raw) > 8192:
            return Response(status_code=204)
        import json

        report = CspReportEnvelope.model_validate(json.loads(raw)).csp_report
        directive = report.effective_directive or report.violated_directive
        # Store the PATH, matching every other error_log row: the origin is this
        # instance by construction, and keeping it whole would make grouping by
        # `signature` split on scheme/host variations.
        doc = (report.document_uri or "").split("?", 1)[0]
        for sep in ("://",):
            if sep in doc:
                doc = "/" + doc.split(sep, 1)[1].split("/", 1)[-1] if "/" in doc.split(sep, 1)[1] else "/"
        event = {
            "source": "csp",
            "exception_type": "CspViolation",
            "message": f"CSP would block {directive or 'a resource'}: "
                       f"{report.blocked_uri or '(inline)'}"[:500],
            "method": "POST",
            "path": doc[:512],
            "status_code": 0,
            "code": "CSP_VIOLATION",
            "ip": ip or None,
            "request_id": getattr(request.state, "request_id", None),
            "user_id": None,
            "auth_via": None,
            "at": utc_now().isoformat(),
        }
        job_queue.enqueue("notify_admin_error", event=event)
    except Exception:
        logger.warning("csp report skipped", exc_info=True)
    return Response(status_code=204)
