"""The one place that answers "who is calling?".

`request.client.host` is inlined at ~29 sites with three different falsy defaults
(`None`, `""`, `"unknown"`). That was harmless while the value only ever landed in
a log. The scan guard is the first control that DENIES service on it, and a block
is only defensible if the address the guard acted on is provably the same one the
admin sees in the error log next to the evidence.

This module does not parse `X-Forwarded-For` and must never start. Resolution
happens in the infrastructure - nginx's `real_ip` block and uvicorn's
`--proxy-headers --forwarded-allow-ips` - exactly as it did before. Adding a
second, application-level interpretation would create two answers to one question,
which is the bug this file exists to prevent.

**Trust caveat, and why `is_blockable` exists.** uvicorn takes the LEFTMOST
`X-Forwarded-For` value, so the address is only as trustworthy as the edge. The
shipped topology is safe (Traefik sets no `forwardedHeaders.trustedIPs`, so it
overwrites the header), but a self-hoster who front-ends with a CDN and trusts it,
or runs nginx on :443 directly, hands the caller control of that value. Blocking
therefore never acts on an address that is not globally routable - see
`is_blockable`, and the note in `docker/traefik/README.md`.
"""
from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any


def get_client_ip(request: Any) -> str | None:
    """The calling address, or None when there isn't one (ASGI scopes without a
    client: lifespan, some test transports)."""
    client = getattr(request, "client", None)
    if client is None:
        return None
    host = getattr(client, "host", None)
    return host or None


def client_ip_from_scope(scope: Mapping[str, Any]) -> str | None:
    """Same answer, straight off a raw ASGI scope.

    Pure-ASGI middleware runs before a `Request` object exists, and building one
    per request just to read `.client` would allocate on the hot path of every
    single request.
    """
    client = scope.get("client")
    if not client:
        return None
    host = client[0] if isinstance(client, (tuple, list)) else None
    return host or None


def is_blockable(ip: str | None) -> bool:
    """Whether ``ip`` may ever be counted against, or blocked.

    Only globally-routable addresses qualify. This is an invariant, not a
    setting, and it is load-bearing in a way that is easy to miss:

    The backend has FIVE distinct peers, not one - Traefik (for `/api/*`), the
    frontend nginx (for everything else, which is where ALL scanner-bait traffic
    arrives), tusd, the updater-executor, and the compose HEALTHCHECK on
    loopback. `docker/traefik/README.md` and CLAUDE.md both advise pinning
    `FORWARDED_ALLOW_IPS` to the proxy CIDR; do that, and uvicorn stops honouring
    `X-Forwarded-For` from nginx, so every bait request resolves to *nginx's own
    container address*. The guard would then see one address producing 100% of
    404s with maximum path diversity - a textbook scanner - block it, and take
    `/api/` down for the entire SPA.

    Refusing every non-global address neutralises that, plus the bridge gateway,
    tusd, the updater, the healthcheck, the e2e suite and CI, in one rule. It
    also removes "spoof `X-Forwarded-For: 10.0.0.1` and get the LAN banned".
    """
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(addr.is_global)
