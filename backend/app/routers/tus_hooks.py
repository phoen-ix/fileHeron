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
- post-finish  → already finalized; we move file + mark ready.
- post-terminate → upload abandoned; release reservation.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, Header, Request
from sqlalchemy.orm import Session

from ..config import settings
from ..dependencies import get_db
from ..middleware.errors import AppError
from ..services import tus_hooks as hooks_svc

logger = logging.getLogger("fileheron.tus_hooks")

router = APIRouter(tags=["internal"])


def _allowed_ips() -> set[str]:
    """Parse TUS_HOOK_ALLOWED_IPS CSV. Empty set = no enforcement."""
    raw = getattr(settings, "TUS_HOOK_ALLOWED_IPS", "") or ""
    return {p.strip() for p in raw.split(",") if p.strip()}


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
    allowed = _allowed_ips()
    if allowed:
        client_ip = request.client.host if request.client else ""
        if client_ip not in allowed:
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
