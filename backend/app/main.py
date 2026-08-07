"""FastAPI app factory. Boot order:
1. Configure logging.
2. Build app + register middleware (security headers / request ID / gzip).
3. Register exception handler (envelope shape).
4. Register routers.

The SPA is served by the separate frontend nginx container; this app does not
serve static files.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .database import SessionLocal
from .middleware.errors import (
    AppError,
    app_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
)
from .middleware.gzip import SelectiveGZipMiddleware
from .middleware.request_id import RequestIdMiddleware
from .middleware.scan_guard import ScanGuardMiddleware
from .middleware.security_headers import SecurityHeadersMiddleware
from .routers import (
    account,
    admin,
    auth,
    branding,
    files,
    groups,
    health,
    metrics,
    notification_subscriptions,
    notifications,
    oidc,
    oidc_connect,
    public,
    public_links,
    setup,
    shares,
    telemetry,
    tus_hooks,
    uploads,
    users,
    webauthn,
)
from .services.admin_bootstrap import bootstrap_admin_if_configured
from .services.twofa_enforcement import require_2fa_complete
from .utils.logger import configure_logging  # noqa: F401
from .version import VERSION as _APP_VERSION


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging(settings.LOG_LEVEL)
    db = SessionLocal()
    try:
        bootstrap_admin_if_configured(db)
        # A postponed update keeps maintenance ON across the hand-off, because
        # the image pull is precisely when a new upload must not start. THIS
        # container starting is the hand-off concluding, so it is what lifts the
        # gate again (audit 2026-07-30, flow-maintenance-5). No-op unless
        # maintenance was set by that flow.
        from .services import maintenance as maintenance_svc
        try:
            maintenance_svc.clear_maintenance_after_update(db)
        except Exception:
            logging.getLogger("fileheron.startup").exception(
                "could not lift post-update maintenance"
            )
    finally:
        db.close()
    yield


_disable_docs = settings.is_production

app = FastAPI(
    title="fileHeron",
    version=_APP_VERSION,
    docs_url=None if _disable_docs else "/docs",
    redoc_url=None if _disable_docs else "/redoc",
    openapi_url=None if _disable_docs else "/openapi.json",
    lifespan=lifespan,
)

# Outer-most first to inner-most: security_headers wraps everything (so error
# responses still get the headers); request_id provides correlation; gzip is
# innermost so error envelopes get compressed too. Gzip is SELECTIVE - it skips
# file-download responses (gzipping a multi-GB binary is pointless + defeats
# FileResponse sendfile, making downloads crawl).
app.add_middleware(SelectiveGZipMiddleware, minimum_size=1024)
# scan_guard sits INSIDE request_id + security_headers on purpose: its refusal
# response then picks up the request id and every security header on the way out,
# which is what makes a blocked request byte-identical to a genuine 404. It is
# also OUTSIDE Starlette's ExceptionMiddleware, so a short-circuited response
# never reaches the error handlers - no error_log row, no ARQ job, no feedback
# loop. See middleware/scan_guard.py.
app.add_middleware(ScanGuardMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware, is_production=settings.is_production)

# Exception handlers - AppError + framework HTTPException → envelope (the latter
# also reaches the error-capture path); everything else → 500 with envelope.
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

# Routers
#
# Routes that should be reachable while 2FA setup is required (so the
# user can actually finish setup, log in, and read /me to learn the
# requirement) are mounted WITHOUT the gate. Everything else gets the
# `require_2fa_complete` dependency, which raises 403 TWOFA_SETUP_REQUIRED
# when the active policy applies to the user and they haven't enabled
# TOTP yet.
_gate = [Depends(require_2fa_complete)]

# Exempt: needs to be reachable for setup / login / health-checks.
app.include_router(health.router)
app.include_router(metrics.router)               # gated internally (scraper token / IP allowlist)
app.include_router(setup.router)                 # anonymous wizard for first admin
app.include_router(auth.router)
app.include_router(account.setup_router)         # /me + /2fa/* + /locale only
app.include_router(public.router)                # anonymous public-link landing
app.include_router(notification_subscriptions.router)  # anonymous, token-authed
app.include_router(branding.router)              # anonymous logo + legal pages
app.include_router(telemetry.router)             # anonymous SPA client-404 beacon
app.include_router(tus_hooks.router)             # internal HMAC-gated
app.include_router(oidc.router)                  # anonymous OIDC login
app.include_router(webauthn.auth_router)         # WebAuthn login flow

# Subject to the gate.
# account.router carries everything /api/account/* EXCEPT the setup routes
# above - notably /invite and the API-token endpoints. Mounting the whole
# module ungated is what made mandatory 2FA bypassable: mint a token from an
# ungated route, then use it everywhere, since require_2fa_complete
# short-circuits for api_token auth (audit 2026-07-30).
app.include_router(account.router, dependencies=_gate)
app.include_router(uploads.router, dependencies=_gate)
app.include_router(files.router, dependencies=_gate)
# files.download_router intentionally NOT gated: the GET /download
# endpoint authenticates via a signed `?dt=` token (browser <a href>)
# OR bearer + inline 2FA check for JWT clients. The gate's
# get_actor() requires the Authorization header, which the signed-URL
# flow can't provide.
app.include_router(files.download_router)
app.include_router(shares.router, dependencies=_gate)
app.include_router(public_links.router, dependencies=_gate)
app.include_router(users.router, dependencies=_gate)
app.include_router(groups.router, dependencies=_gate)
app.include_router(notifications.router, dependencies=_gate)
# notifications.stream_router intentionally NOT gated: the SSE endpoint
# auths via a signed `?token=` (EventSource can't send Authorization
# headers) OR bearer for curl/CI. Same pattern as files.download_router.
app.include_router(notifications.stream_router)
app.include_router(admin.router, dependencies=_gate)
app.include_router(oidc_connect.router, dependencies=_gate)
# The connect callback is a top-level IdP browser redirect (cookies only, no
# Bearer); it authenticates via the signed state cookie, so it stays OUTSIDE _gate.
app.include_router(oidc_connect.callback_router)
app.include_router(webauthn.account_router, dependencies=_gate)
