"""Healthcheck + public config. The public-config endpoint is what the
SPA hits BEFORE login to know which OIDC providers (if any) to render
buttons for."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..dependencies import get_db
from ..services import oidc as oidc_svc

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health_check() -> JSONResponse:
    """Service-readiness probe.

    DB outage → 503 (the app is unusable). Redis / ClamAV outages
    are reported as `degraded` subfields but the response stays 200
    — the app still serves JWT requests with rate-limit and AV
    fail-open semantics. Operators should alert on `degraded` even
    when status is `ok`."""
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "db_unavailable"})

    degraded: list[str] = []

    # Redis (rate limit + quota) — failure is non-fatal but worth surfacing.
    try:
        from ..redis_client import get_redis
        get_redis().ping()
    except Exception:
        degraded.append("redis")

    # ClamAV — only ping when AV scanning is on. Scoped import to avoid
    # pulling the AV module in test contexts that monkeypatch it.
    if not getattr(settings, "AV_SKIP", False):
        try:
            from ..services import av_scan
            if not av_scan.ping():
                degraded.append("clamav")
        except Exception:
            degraded.append("clamav")

    body: dict = {"status": "ok"}
    if degraded:
        body["degraded"] = degraded
    return JSONResponse(status_code=200, content=body)


@router.get("/api/config-public")
def public_config(db: Session = Depends(get_db)) -> dict:
    """Public, no auth. Returns enough for the login surface to render.
    Phase 10: returns one entry per **enabled and usable** OIDC provider
    so the SPA renders one button per. Disabled or unconfigured providers
    are excluded."""
    providers = []
    for p in oidc_svc.list_enabled_providers(db):
        if not oidc_svc.is_provider_usable(p):
            continue
        providers.append(
            {
                "id": p.id,
                "name": p.name,
                "preset": p.preset.value,
            }
        )
    return {
        "app_name": settings.APP_NAME,
        "default_locale": "en",
        "providers": providers,
    }
