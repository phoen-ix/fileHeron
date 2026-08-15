"""tusd webhook receiver. Internal-only - only reachable from inside the
Docker `internal` network.

Auth (defense in depth):
1. HMAC envelope embedded in TUS Upload-Metadata (forwarded by tusd via
   ``-hooks-http-forward-headers=Upload-Metadata``). Validated by
   services.tus_signing on every event. This is the load-bearing check.
2. Optional source-IP allowlist enforced here. Set
   ``TUS_HOOK_ALLOWED_IPS`` (CSV of IPs / CIDR-less hosts) to require
   the request's client to come from an expected source - typically
   the tusd container's IP. Empty (default) accepts anything; the
   HMAC envelope is still required.

**Critical operator note**: ``/api/internal/*`` MUST never be exposed
by the reverse proxy. Traefik should not have a route that matches
this prefix. The path is internal-only by convention, not by network
isolation alone - review your proxy config.

Behaviour by hook event:
- pre-create   → return 200 to allow, 4xx to reject.
- pre-finish   → final last-chance to reject.
- post-receive → progress tick while bytes are arriving; stamps
  ``files.last_progress_at`` so the sweepers can tell a live multi-hour upload
  from an abandoned one. Fires per ``-progress-hooks-interval`` (30s), so it
  must stay cheap and must never raise.
- post-finish  → already finalized; we move file + mark ready.
- post-terminate → upload abandoned; release reservation.
"""
from __future__ import annotations

import ipaddress
import logging

from fastapi import APIRouter, Body, Depends, Header, Request
from sqlalchemy.orm import Session

from ..config import settings
from ..dependencies import get_db
from ..middleware.errors import AppError
from ..services import tus_hooks as hooks_svc

logger = logging.getLogger("fileheron.tus_hooks")

router = APIRouter(tags=["internal"])


def _allowlist_configured() -> bool:
    return bool((getattr(settings, "TUS_HOOK_ALLOWED_IPS", "") or "").strip())


def _ip_allowed(client_ip: str | None) -> bool:
    """True if `client_ip` matches TUS_HOOK_ALLOWED_IPS (CSV of addresses
    and/or CIDR ranges). Mirrors routers/metrics.py::_ip_allowed.

    CIDR support matters operationally: this allowlist used to accept bare
    addresses only, and a Docker container's IP is not stable across a
    recreate, so an operator who set it to tusd's current address would have
    every upload start failing the next time the container moved. With CIDR you
    can allow the compose network (e.g. 172.18.0.0/16) and it keeps working.
    That was the blocker on turning this on at all (audit 2026-07-30), and it
    is the only control on /api/internal/* that does not depend on the reverse
    proxy's path handling - a PathPrefix deny in Traefik is bypassable with a
    percent-encoded slash when the entrypoint permits encoded slashes.
    """
    raw = (getattr(settings, "TUS_HOOK_ALLOWED_IPS", "") or "").strip()
    if not raw:
        return True  # not configured = no enforcement (HMAC still required)
    if not client_ip:
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


@router.post("/api/internal/tus-hooks", status_code=200)
def tus_hooks(
    request: Request,
    body: dict = Body(...),
    hook_name: str | None = Header(default=None, alias="Hook-Name"),
    db: Session = Depends(get_db),
) -> dict:
    """Dispatch on the hook name. tusd may send the name via either:
    - Body['Type'] (modern tusd)
    - Hook-Name header (older tusd)
    We accept both.
    """
    if _allowlist_configured():
        client_ip = request.client.host if request.client else ""
        if not _ip_allowed(client_ip):
            logger.warning(
                "tus hook rejected: source ip %s not in TUS_HOOK_ALLOWED_IPS",
                client_ip or "<unknown>",
            )
            raise AppError(
                403, "TUS_HOOK_FORBIDDEN", "Hook source not allowed."
            )

    name = body.get("Type") or hook_name or ""
    name = name.strip().lower()

    handler = hooks_svc.HOOK_DISPATCH.get(name)
    if handler is None:
        # Some events (e.g. post-create) we don't care about.
        logger.debug("unhandled tus hook: %s", name)
        return {"ok": True, "ignored": True, "name": name}

    try:
        handler(db, body)
    except AppError:
        raise
    except Exception:
        logger.exception("tus hook handler %s failed", name)
        raise AppError(500, "HOOK_HANDLER_ERROR", f"Hook {name} handler error.") from None

    return {"ok": True, "name": name}
