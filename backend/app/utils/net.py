"""SSRF guards for admin-influenced outbound fetches.

Several endpoints fetch admin-controlled URLs server-side: OIDC discovery /
token / JWKS (issuer URL configured by an admin) and the self-update release
check (`updates.api_url`). Without a guard, an admin (or a compromised admin
session) can point these at `http://169.254.169.254/...` (cloud metadata),
`http://127.0.0.1:6379/` (internal Redis), etc. — classic SSRF.

`assert_public_http_url` resolves the host and rejects loopback, link-local
(incl. the 169.254.169.254 metadata range), multicast, reserved and
unspecified addresses **always**. Private LAN ranges (10/8, 172.16/12,
192.168/16, ULA) are rejected unless `allow_private=True` — which the OIDC
path sets, because self-hosting an IdP (Keycloak/Authentik) on the same
private network is a legitimate and common deployment.

This is resolve-then-check: it stops the common static-IP / DNS-to-internal
cases. A determined TOCTOU DNS-rebind between this check and httpx's own
resolution is out of scope (would require pinning the connection to the
validated IP); the realistic admin-SSRF vectors are covered.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from ..middleware.errors import AppError


def assert_public_http_url(
    url: str, *, allow_private: bool = False, require_https: bool = True
) -> None:
    """Raise AppError(400) if `url` is not a safe outbound target."""
    parsed = urlparse(url or "")
    scheme = (parsed.scheme or "").lower()
    if require_https:
        if scheme != "https":
            raise AppError(400, "URL_NOT_ALLOWED", "URL must use https.")
    elif scheme not in ("http", "https"):
        raise AppError(400, "URL_NOT_ALLOWED", "URL must use http or https.")

    host = parsed.hostname
    if not host:
        raise AppError(400, "URL_NOT_ALLOWED", "URL has no host.")

    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise AppError(400, "URL_BLOCKED", f"Could not resolve host: {host}") from e

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        # Always blocked — never a legitimate IdP / release host.
        if (
            ip.is_loopback
            or ip.is_link_local  # incl. 169.254.169.254 cloud-metadata
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise AppError(
                400, "URL_BLOCKED", "URL resolves to a non-routable address."
            )
        # Private LAN ranges: blocked unless explicitly permitted.
        if not allow_private and ip.is_private:
            raise AppError(
                400, "URL_BLOCKED", "URL resolves to a private address."
            )
