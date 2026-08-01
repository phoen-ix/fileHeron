"""Healthcheck + public config. The public-config endpoint is what the
SPA hits BEFORE login to know which OIDC providers (if any) to render
buttons for."""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
from functools import lru_cache

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..dependencies import get_db
from ..services import oidc_admin as oidc_admin_svc
from ..version import GIT_SHA, VERSION

logger = logging.getLogger("fileheron.health")

router = APIRouter(tags=["health"])



# Cache window for the non-authoritative dependency probes. Short enough that a
# real outage surfaces within seconds, long enough that a burst of health checks
# cannot amplify into a burst of Redis/clamd connections.
_PROBE_CACHE_TTL_SEC = 5.0
_probe_cache: tuple[float, list[str]] | None = None


def _cached_dependency_probes() -> list[str]:
    """Redis + ClamAV liveness, memoised for _PROBE_CACHE_TTL_SEC."""
    global _probe_cache
    import time as _time

    now = _time.monotonic()
    if _probe_cache is not None and (now - _probe_cache[0]) < _PROBE_CACHE_TTL_SEC:
        return list(_probe_cache[1])

    found: list[str] = []
    try:
        from ..redis_client import get_redis
        get_redis().ping()
    except Exception:
        found.append("redis")

    # Only ping clamd when AV scanning is on. Scoped import so test contexts
    # that monkeypatch the AV module are not forced to import it here.
    if not getattr(settings, "AV_SKIP", False):
        try:
            from ..services import av_scan
            if not av_scan.ping():
                found.append("clamav")
        except Exception:
            found.append("clamav")

    _probe_cache = (now, list(found))
    return found


def _peer_is_operator(request: Request) -> bool:
    """True when the caller reached us over loopback or the compose network.

    The diagnostic half of this response is operator information, and
    /api/health is anonymous with the whole of /api/ proxied from the internet.
    fileHeron is published for public self-hosting against a public repo, so
    `running_sha` mapped one-to-one onto a known source tree and told any
    passer-by exactly which security fixes an instance was still missing, while
    `degraded` told them when Redis was down and the per-IP limits had fallen
    back to the weaker in-process limiter - precisely when to start credential
    stuffing. Every consumer that needs the detail (the compose HEALTHCHECK, the
    updater executor's running_version poll, an operator on the box) arrives
    over loopback or the docker bridge; public callers get liveness only. This
    trusts request.client.host, which is only as good as the proxy's
    X-Forwarded-For handling - the same assumption the audit log and the rate
    limiter already make (audit 2026-07-30).
    """
    host = request.client.host if request.client else None
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    return any(addr in net for net in _trusted_networks())


@lru_cache(maxsize=1)
def _trusted_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """The networks that count as "the compose network".

    This used to accept EVERY RFC1918 address - 10/8, 172.16/12 and 192.168/16,
    plus link-local - which is not what the docstring above says and is not the
    same set. On a host whose LAN is 192.168.0.0/24, or behind a proxy that
    forwards a private client address, the diagnostic body went to callers that
    are not operators at all (audit #2).

    Derived from this container's OWN address, so the two real consumers keep
    working: the compose HEALTHCHECK arrives over loopback, and the updater
    executor arrives from a sibling container on the same compose network.
    `HEALTH_DETAIL_TRUSTED_CIDRS` (comma-separated) overrides it for a
    deployment that puts them somewhere else.
    """
    raw = os.environ.get("HEALTH_DETAIL_TRUSTED_CIDRS", "").strip()
    if raw:
        out = []
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                out.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                logger.warning("ignoring unparseable HEALTH_DETAIL_TRUSTED_CIDRS entry %r", item)
        return tuple(out)
    try:
        own = ipaddress.ip_address(socket.gethostbyname(socket.gethostname()))
    except Exception:
        return ()
    if own.is_loopback:
        return ()
    # Compose allocates its networks out of /16 pools, so the container's own
    # /16 usually contains its siblings - EXCEPT on a host-network deployment,
    # where the container's address is the host's LAN address and a /16 would
    # re-admit the whole 192.168.x.x LAN: the exact set this check was written
    # to stop trusting (audit #2 cross-check). Docker's default pools live in
    # 172.16/12 and 10/8; a 192.168 address is a LAN, so bound it to its /24.
    prefix = 24 if own in ipaddress.ip_network("192.168.0.0/16") else 16
    return (ipaddress.ip_network(f"{own}/{prefix}", strict=False),)


@router.get("/api/health")
def health_check(request: Request) -> JSONResponse:
    """Service-readiness probe.

    DB outage → 503 (the app is unusable). Redis / ClamAV outages
    are reported as `degraded` subfields but the response stays 200
    - the app still serves JWT requests with rate-limit and AV
    fail-open semantics. Operators should alert on `degraded` even
    when status is `ok`. Build identifiers, pool stats and `degraded`
    render only for loopback / compose-network callers - see
    `_peer_is_operator`; the public path gets bare liveness."""
    detailed = _peer_is_operator(request)
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
        body_503: dict = {"status": "db_unavailable"}
        if detailed:
            body_503["running_version"] = VERSION
            body_503["running_sha"] = GIT_SHA
        return JSONResponse(status_code=503, content=body_503)

    if not detailed:
        return JSONResponse(status_code=200, content={"status": "ok"})

    degraded: list[str] = []

    # Redis + ClamAV probes are CACHED for a few seconds. /api/health is
    # anonymous and unthrottled, and each call otherwise opened a Redis
    # connection and a clamd TCP session - so anyone could drive unbounded
    # backend work from an unauthenticated endpoint, and a slow dependency
    # multiplied it (audit 2026-07-30). A liveness signal a few seconds stale is
    # exactly as useful, and container HEALTHCHECK/uptime pollers are unaffected
    # because the DB check (the one that decides 200 vs 503) still runs every
    # call.
    degraded.extend(_cached_dependency_probes())

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
    from ..services import settings_registry
    from ..services import site as site_svc
    motd: dict | None = None
    if settings_svc.get_bool(db, settings_svc.Keys.MOTD_ENABLED, default=False):
        text = (settings_svc.get(db, settings_svc.Keys.MOTD_TEXT) or "").strip()
        if text:
            motd = {"text": text}

    has_logo = bool(settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_LOCATOR))
    branding = {
        "logo_url": "/api/branding/logo" if has_logo else None,
        "link_url": settings_svc.get(db, settings_svc.Keys.BRANDING_LINK_URL) or None,
        "show_header": settings_svc.get_bool(db, settings_svc.Keys.BRANDING_SHOW_HEADER, default=False),
        "show_login": settings_svc.get_bool(db, settings_svc.Keys.BRANDING_SHOW_LOGIN, default=False),
        "show_public": settings_svc.get_bool(db, settings_svc.Keys.BRANDING_SHOW_PUBLIC, default=False),
    }
    legal = {
        "imprint_enabled": settings_svc.get_bool(db, settings_svc.Keys.LEGAL_IMPRINT_ENABLED, default=False),
        "privacy_enabled": settings_svc.get_bool(db, settings_svc.Keys.LEGAL_PRIVACY_ENABLED, default=False),
    }

    # Maintenance mode: surface the flag (+ optional banner message) so the SPA
    # can show a global banner and disable transfer UI. Absent when off.
    maintenance: dict | None = None
    if settings_svc.get_bool(db, settings_svc.Keys.MAINTENANCE_ENABLED, default=False):
        maintenance = {
            "enabled": True,
            "message": (settings_svc.get(db, settings_svc.Keys.MAINTENANCE_MESSAGE) or "").strip(),
        }

    body: dict = {
        "app_name": site_svc.get_app_name(db),
        "default_locale": "en",
        "providers": providers,
        # No `running_version` here. /api/health hides the build identifiers
        # from anonymous callers because they map one-to-one onto a public
        # source tree and say exactly which security fixes an instance is
        # missing - and this endpoint, which is anonymous by design, handed the
        # same fact to anyone who asked, defeating that gate entirely. Nothing
        # in the SPA rendered it; the admin surface reads its version from
        # /api/admin/system/status (audit #2).
        # The LIVE direct-upload ceiling. The SPA used to decide direct vs
        # resumable from a build-time constant, so an admin who lowered
        # `uploads.max_direct_bytes` on a small VPS made every mid-size upload
        # stream the whole file and then fail with "too large for a direct
        # upload", repeatably, for every user - while a much bigger file worked
        # (audit #2). Not a secret: it is the number the client has to respect.
        "max_direct_upload_bytes": int(
            settings_registry.effective(db, settings_registry.K.MAX_DIRECT_UPLOAD_BYTES)
        ),
        "site_timezone": site_svc.get_site_timezone(db),
        "branding": branding,
        "legal": legal,
    }
    if motd is not None:
        body["motd"] = motd
    if maintenance is not None:
        body["maintenance"] = maintenance
    return body
