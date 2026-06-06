"""Prometheus metrics — GET /api/metrics (text/plain exposition format).

Hand-rendered (no prometheus_client dep). System-level gauges only — no
per-user series, to keep cardinality bounded. The endpoint is gated INSIDE the
handler (not via the 2FA/bearer router stack) so a scraper can reach it with a
dedicated token or from an allow-listed IP:

  Authorization: Bearer <METRICS_BEARER_TOKEN>   OR   client IP ∈ METRICS_ALLOWED_IPS

With both config values empty the endpoint is effectively disabled (401 on
every request). The rendered body is cached in Redis for METRICS_CACHE_TTL_SEC
so frequent scrapes don't hammer the DB; cache failures fall back to a fresh
compute (fail-open).
"""
from __future__ import annotations

import hmac
import ipaddress
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..database import pool_stats
from ..dependencies import get_db
from ..middleware.errors import AppError
from ..models.file import File, FileState
from ..models.share import Share, ShareState
from ..models.user import User
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.metrics")

router = APIRouter(tags=["metrics"])

_CACHE_KEY = "fh:metrics:cached"


def _ip_allowed(client_ip: str | None) -> bool:
    raw = (settings.METRICS_ALLOWED_IPS or "").strip()
    if not raw or not client_ip:
        return False
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "/" in part:
                if addr in ipaddress.ip_network(part, strict=False):
                    return True
            elif addr == ipaddress.ip_address(part):
                return True
        except ValueError:
            continue
    return False


def _bearer_allowed(authorization: str | None) -> bool:
    token = (settings.METRICS_BEARER_TOKEN or "").strip()
    if not token or not authorization:
        return False
    if not authorization.lower().startswith("bearer "):
        return False
    presented = authorization.split(" ", 1)[1].strip()
    return hmac.compare_digest(presented, token)


def _line(name: str, value, *, help_: str, type_: str = "gauge") -> str:
    return f"# HELP {name} {help_}\n# TYPE {name} {type_}\n{name} {value}\n"


def _render(db: Session) -> str:
    cutoff_24h = utc_now() - timedelta(hours=24)

    # --- storage (DB-authoritative used bytes; statvfs for disk free/total) ---
    storage_used = int(
        db.query(func.coalesce(func.sum(File.size_bytes), 0))
        .filter(
            File.state.in_(
                [FileState.uploading, FileState.ready_unscanned, FileState.clean]
            )
        )
        .scalar()
        or 0
    )
    from ..services import storage as storage_svc

    disk = storage_svc.get_disk_stats(settings.STORAGE_ROOT)

    # --- counts ---
    users_total = db.query(func.count(User.id)).scalar() or 0
    users_active_24h = (
        db.query(func.count(User.id))
        .filter(User.last_login_at.isnot(None), User.last_login_at >= cutoff_24h)
        .scalar()
        or 0
    )
    users_disabled = (
        db.query(func.count(User.id)).filter(User.is_disabled.is_(True)).scalar() or 0
    )
    shares_active = (
        db.query(func.count(Share.id)).filter(Share.state == ShareState.active).scalar()
        or 0
    )
    files_clean = (
        db.query(func.count(File.id)).filter(File.state == FileState.clean).scalar() or 0
    )
    files_quarantined = (
        db.query(func.count(File.id)).filter(File.state == FileState.infected).scalar()
        or 0
    )

    # --- service health (1 = ok, 0 = down) ---
    db_status = 1  # we are inside a working DB session
    try:
        from ..redis_client import get_redis

        get_redis().ping()
        redis_status = 1
    except Exception:
        redis_status = 0
    if getattr(settings, "AV_SKIP", False):
        clamav_status = 1
    else:
        try:
            from ..services import av_scan

            clamav_status = 1 if av_scan.ping() else 0
        except Exception:
            clamav_status = 0

    parts: list[str] = [
        _line("fileheron_storage_used_bytes", storage_used,
              help_="Bytes allocated by non-deleted files (DB sum)."),
        _line("fileheron_storage_free_bytes", disk.get("free_bytes", 0),
              help_="Free bytes on the storage volume."),
        _line("fileheron_storage_total_bytes", disk.get("total_bytes", 0),
              help_="Total bytes on the storage volume."),
        _line("fileheron_users_total", users_total, help_="Total user accounts."),
        _line("fileheron_users_active_24h", users_active_24h,
              help_="Users with a login in the last 24h."),
        _line("fileheron_users_disabled", users_disabled, help_="Disabled user accounts."),
        _line("fileheron_shares_active", shares_active, help_="Shares in the active state."),
        _line("fileheron_files_clean", files_clean, help_="Files in the clean state."),
        _line("fileheron_files_quarantined", files_quarantined,
              help_="Files in the infected/quarantined state."),
        _line("fileheron_db_status", db_status, help_="Database reachable (1) or not (0)."),
        _line("fileheron_redis_status", redis_status, help_="Redis reachable (1) or not (0)."),
        _line("fileheron_clamav_status", clamav_status,
              help_="ClamAV reachable/skipped (1) or down (0)."),
    ]

    pool = pool_stats()
    if pool is not None:
        parts.append(_line("fileheron_db_pool_size", pool["size"],
                           help_="Configured DB connection pool size."))
        parts.append(_line("fileheron_db_pool_overflow", pool["overflow"],
                           help_="Live overflow connections beyond pool size."))
        parts.append(_line("fileheron_db_pool_checked_out", pool["checked_out"],
                           help_="DB connections currently checked out."))

    return "".join(parts)


@router.get("/api/metrics", response_class=PlainTextResponse)
def metrics(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> PlainTextResponse:
    client_ip = request.client.host if request.client else None
    if not (_bearer_allowed(authorization) or _ip_allowed(client_ip)):
        raise AppError(401, "AUTH_REQUIRED", "Metrics require a scraper token or allow-listed IP.")

    # Cache read (fail-open).
    try:
        from ..redis_client import get_redis

        cached = get_redis().get(_CACHE_KEY)
        if cached:
            return PlainTextResponse(cached, media_type="text/plain; version=0.0.4")
    except Exception:
        pass

    body = _render(db)

    try:
        from ..redis_client import get_redis

        ttl = max(1, int(settings.METRICS_CACHE_TTL_SEC))
        get_redis().set(_CACHE_KEY, body, ex=ttl)
    except Exception:
        pass

    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")
