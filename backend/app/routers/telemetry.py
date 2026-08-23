"""Anonymous client telemetry.

Two beacons, both anonymous, both fire-and-forget, both capped per IP AND
globally.

They do NOT ride the same switch, and the difference matters because the two
defaults are opposite: the SPA 404 beacon is gated on `error_log.capture_4xx`
(default OFF), the CSP sink on `error_log.enabled` (default ON) - see the block
above `csp_report` for why. This header claimed they shared one switch, which is
the kind of assumed symmetry that let the beacon go without a global cap while
the sink beside it had one.

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

from ..middleware.errors import _redact_path
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


def _redact_uri(uri: str) -> str:
    """Strip the query string and collapse any token segment of a URI, keeping
    scheme+host so an operator can still tell first-party from third-party."""
    if not uri:
        return ""
    base = uri.split("?", 1)[0]
    if "://" not in base:
        return _redact_path(base)[:512]
    scheme, rest = base.split("://", 1)
    host, _, path = rest.partition("/")
    return f"{scheme}://{host}{_redact_path('/' + path) if path else ''}"[:512]


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
        # Global ceiling as well as the per-IP one. The per-IP cap bounds one
        # source at 10/min and nothing bounded the sum, so N sources wrote
        # error_log rows and ARQ jobs without limit. `error_alert`'s hourly cap
        # is not a substitute - it bounds outbound alert MAIL, not rows or jobs.
        # Same shape and the same tunable as the CSP sink below and the 4xx
        # pre-guard in middleware/errors.py.
        if not rate_limit.check_ip_allowed(
            "client_404_global",
            "global",
            limit=error_log.capture_rate_per_min_cached(),
            window_sec=60,
        ):
            return Response(status_code=204)
        # Path only - drop the query string (may carry junk/tokens), collapse
        # any token segment, then truncate. The path is client-asserted, and
        # the SPA's own token routes are exactly the ones a user mistypes into
        # a 404 (audit #2).
        path = _redact_path((body.path or "").split("?", 1)[0])[:512]
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
        # A GLOBAL ceiling as well as the per-IP one. This said it was "the
        # only error-capture entry point that had no aggregate cap" - true when
        # written, and then not: the SPA 404 beacon above still matched that
        # description for two releases, describing its own fix while its
        # neighbour went uncapped. Both have one now. It matters most here
        # because this sink is on by default: on the s3 backend every preview
        # is a 307 to a presigned bucket URL, which the shipped policy does not
        # allow, so twenty people browsing shares produce hundreds of reports a
        # minute - each an error_log row and an ARQ job. The one screen the
        # "enforce once the reports come back empty" criterion is read from
        # would be the first thing drowned (audit #2). Same shape and the same
        # tunable as the 4xx pre-guard.
        if not rate_limit.check_ip_allowed(
            "csp_report_global",
            "global",
            limit=error_log.capture_rate_per_min_cached(),
            window_sec=60,
        ):
            return Response(status_code=204)
        # Content-Length BEFORE the read. `await request.body()` materialises
        # the whole body, so checking its length afterwards is a cap that has
        # already paid the cost it exists to avoid - and nginx allows 1024m on
        # /api/ for the direct-upload path. The edge now caps /api/telemetry/
        # at 64k, which is the real bound (and the only one that can cover
        # /page-404, whose Pydantic body model is buffered by FastAPI before
        # any handler code runs); this is the in-process half, for a request
        # that reaches the app another way.
        #
        # A missing or unparseable Content-Length falls through to the read,
        # which the post-read check still bounds - chunked bodies carry no
        # length, and refusing them outright would be a behaviour change for a
        # beacon that is meant to be lossy.
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > 8192:
                    return Response(status_code=204)
            except ValueError:
                pass
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
        doc = _redact_path(doc)
        event = {
            "source": "csp",
            "exception_type": "CspViolation",
            # The blocked URI is browser-supplied and can be a same-origin
            # URL - so it gets the same token collapsing as the document URI.
            "message": f"CSP would block {directive or 'a resource'}: "
                       f"{_redact_uri(report.blocked_uri) or '(inline)'}"[:500],
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
