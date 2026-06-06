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
from ..services import oidc_admin as oidc_admin_svc
from ..version import GIT_SHA, VERSION

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health_check() -> JSONResponse:
    """Service-readiness probe.

    DB outage → 503 (the app is unusable). Redis / ClamAV outages
    are reported as `degraded` subfields but the response stays 200
    — the app still serves JWT requests with rate-limit and AV
    fail-open semantics. Operators should alert on `degraded` even
    when status is `ok`."""
    db_latency_ms: float | None = None
    try:
        import time

        db = SessionLocal()
        try:
            t0 = time.monotonic()
            db.execute(text("SELECT 1"))
            db_latency_ms = round((time.monotonic() - t0) * 1000, 2)
        finally:
            db.close()
    except Exception:
        return JSONResponse(
            status_code=503,
            content={
                "status": "db_unavailable",
                "running_version": VERSION,
                "running_sha": GIT_SHA,
            },
        )

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

    body: dict = {
        "status": "ok",
        "running_version": VERSION,
        "running_sha": GIT_SHA,
    }

    from ..database import pool_stats
    pool = pool_stats()
    if pool is not None:
        pool["latency_ms"] = db_latency_ms
        body["db_pool"] = pool

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
    for p in oidc_admin_svc.list_enabled_providers(db):
        if not oidc_admin_svc.is_provider_usable(p):
            continue
        providers.append(
            {
                "id": p.id,
                "name": p.name,
                "preset": p.preset.value,
            }
        )
    # Surface the admin-set login banner if enabled + non-empty. The
    # anonymous SPA login view renders it above the form. Stays absent
    # from the response when disabled so the SPA doesn't render an
    # empty notice.
    from ..services import settings as settings_svc
    from ..services import site as site_svc
    motd: dict | None = None
    if settings_svc.get_bool(db, settings_svc.Keys.MOTD_ENABLED, default=False):
        text = (settings_svc.get(db, settings_svc.Keys.MOTD_TEXT) or "").strip()
        if text:
            motd = {"text": text}

    body: dict = {
        "app_name": site_svc.get_app_name(db),
        "default_locale": "en",
        "providers": providers,
        "running_version": VERSION,
        "site_timezone": site_svc.get_site_timezone(db),
    }
    if motd is not None:
        body["motd"] = motd
    return body
