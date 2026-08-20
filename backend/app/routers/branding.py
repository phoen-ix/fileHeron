"""Anonymous branding + legal endpoints.

- ``GET /api/branding/logo`` serves the admin-uploaded logo to anyone (login
  page, public-link pages, and embedded in emails by absolute URL).
- ``GET /api/legal/{kind}`` returns the sanitised Imprint / Privacy HTML for the
  footer pages.

Both are ungated (mounted without the auth gate, like ``routers/public.py``):
the logo and legal text are public by nature.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..dependencies import get_db
from ..middleware.errors import AppError
from ..schemas.legal_settings import LegalContentResponse
from ..services import richtext
from ..services import settings as settings_svc
from ..services.storage_backend import get_storage_backend, serve_response

router = APIRouter(tags=["branding"])

# Logos change rarely; let browsers + mail proxies cache for a day.
_LOGO_CACHE_SEC = 24 * 60 * 60

_LEGAL_KINDS = {
    "imprint": (
        settings_svc.Keys.LEGAL_IMPRINT_ENABLED,
        settings_svc.Keys.LEGAL_IMPRINT_EN,
        settings_svc.Keys.LEGAL_IMPRINT_DE,
    ),
    "privacy": (
        settings_svc.Keys.LEGAL_PRIVACY_ENABLED,
        settings_svc.Keys.LEGAL_PRIVACY_EN,
        settings_svc.Keys.LEGAL_PRIVACY_DE,
    ),
}


@router.get("/api/branding/logo")
def get_branding_logo(db: Session = Depends(get_db)) -> Response:
    locator = settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_LOCATOR)
    if not locator:
        raise AppError(404, "LOGO_NOT_FOUND", "No logo configured.")
    backend = get_storage_backend()
    if not backend.exists(locator):
        raise AppError(404, "LOGO_NOT_FOUND", "No logo configured.")
    filename = settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_FILENAME) or "logo"
    content_type = (
        settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_CONTENT_TYPE) or "image/png"
    )
    return serve_response(
        backend,
        locator=locator,
        filename=filename,
        mime_type=content_type,
        ttl_sec=_LOGO_CACHE_SEC,
        disposition="inline",
        extra_headers={"Cache-Control": f"public, max-age={_LOGO_CACHE_SEC}"},
    )


@router.get("/api/branding/logo.png")
def get_branding_logo_png(db: Session = Depends(get_db)) -> Response:
    """Header-sized PNG rendition for the desktop client. Gated by the
    ``show_client`` toggle so the client only needs a 200/404 check - returns
    404 when the client surface is off or no logo (PNG) is stored."""
    if not settings_svc.get_bool(db, settings_svc.Keys.BRANDING_SHOW_CLIENT, default=False):
        raise AppError(404, "LOGO_NOT_FOUND", "No client logo configured.")
    locator = settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_PNG_LOCATOR)
    if not locator:
        raise AppError(404, "LOGO_NOT_FOUND", "No client logo configured.")
    backend = get_storage_backend()
    if not backend.exists(locator):
        raise AppError(404, "LOGO_NOT_FOUND", "No client logo configured.")
    return serve_response(
        backend,
        locator=locator,
        filename="logo.png",
        mime_type="image/png",
        ttl_sec=_LOGO_CACHE_SEC,
        disposition="inline",
        extra_headers={"Cache-Control": f"public, max-age={_LOGO_CACHE_SEC}"},
    )


@router.get("/api/legal/{kind}", response_model=LegalContentResponse)
def get_legal(kind: str, db: Session = Depends(get_db)) -> LegalContentResponse:
    keys = _LEGAL_KINDS.get(kind)
    if keys is None:
        raise AppError(404, "UNKNOWN_LEGAL_KIND", "Unknown legal page.")
    enabled_key, en_key, de_key = keys
    enabled = settings_svc.get_bool(db, enabled_key, default=False)
    if not enabled:
        # `enabled` was computed and then not used as a gate: the body was
        # served regardless, and the only thing honouring the flag was
        # LegalPage.vue. So `curl /api/legal/imprint` returned an unpublished
        # draft - which is exactly where a company address, a DPO name or a
        # not-yet-agreed legal position lives. The admin editor reads
        # /admin/settings/legal, not this route, so no preview breaks.
        return LegalContentResponse(enabled=False, html_en="", html_de="")
    return LegalContentResponse(
        enabled=enabled,
        # Stored content is already sanitised on save; sanitise again on serve
        # as defense-in-depth (a stricter allowlist could ship later).
        html_en=richtext.sanitize_html(settings_svc.get(db, en_key)),
        html_de=richtext.sanitize_html(settings_svc.get(db, de_key)),
    )
