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
isolation alone - review your proxy config. The hook itself answers only its
literal, unencoded path (`_reached_by_its_literal_path`), so an encoded slash
that slips past a proxy's PathPrefix deny is refused here.

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

import logging

from fastapi import APIRouter, Body, Depends, Header, Request
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import settings
from ..dependencies import get_db
from ..middleware.errors import AppError
from ..schemas.upload import TusHookResponse
from ..services import tus_hooks as hooks_svc
from ..utils.client_ip import get_client_ip
from ..utils.net import ip_in_allowlist

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
    return ip_in_allowlist(client_ip, raw)


HOOK_PATH = "/api/internal/tus-hooks"


def _reached_by_its_literal_path(request: Request) -> bool:
    """Whether the request line named the hook path byte for byte.

    uvicorn percent-DECODES the path before routing, so `/api/internal%2Ftus-hooks`
    lands here too, while a proxy deny rule written as PathPrefix(`/api/internal/`)
    does not match it whenever the entrypoint permits encoded slashes (Traefik's
    `allowEncodedSlash`) - measured on the reference host on 2026-11-19, where it
    left TUS_HOOK_ALLOWED_IPS as the only thing in the way. tusd always sends
    the literal path, so anything else is refused like an unknown route, and the
    proxy's path handling stops mattering. A server that supplies no raw path
    gives nothing to compare; the decoded route already matched."""
    raw = request.scope.get("raw_path")
    if raw is None:
        return True
    return raw.split(b"?", 1)[0] == HOOK_PATH.encode()


@router.post(HOOK_PATH, response_model=TusHookResponse, status_code=200)
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
    if not _reached_by_its_literal_path(request):
        raise StarletteHTTPException(status_code=404)
    if _allowlist_configured():
        client_ip = get_client_ip(request) or ""
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
