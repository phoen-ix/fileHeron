"""FastAPI app factory. Boot order:
1. Configure logging.
2. Build app + register middleware (security headers / request ID / gzip).
3. Register exception handler (envelope shape).
4. Register routers.

The SPA is served by the separate nginx-spa container; this app does not serve
static files.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from .config import settings
from .database import SessionLocal
from .middleware.errors import AppError, app_error_handler, unhandled_exception_handler
from .middleware.gzip import SelectiveGZipMiddleware
from .middleware.request_id import RequestIdMiddleware
from .middleware.security_headers import SecurityHeadersMiddleware
from .routers import (
    account,
    admin,
    auth,
    files,
    groups,
    health,
    metrics,
    notifications,
    oidc,
    oidc_connect,
    public,
    public_links,
    setup,
    shares,
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
app.add_middleware(RequestIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware, is_production=settings.is_production)

# Exception handlers - AppError envelopes; everything else → 500 with envelope.
app.add_exception_handler(AppError, app_error_handler)
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
app.include_router(account.router)               # /me + /2fa/* must be reachable
app.include_router(public.router)                # anonymous public-link landing
app.include_router(tus_hooks.router)             # internal HMAC-gated
app.include_router(oidc.router)                  # anonymous OIDC login
app.include_router(webauthn.auth_router)         # WebAuthn login flow

# Subject to the gate.
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
app.include_router(webauthn.account_router, dependencies=_gate)
