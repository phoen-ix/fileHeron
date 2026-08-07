"""SSRF guards for admin-influenced outbound fetches.

Several endpoints fetch admin-controlled URLs server-side: OIDC discovery /
token / JWKS (issuer URL configured by an admin) and the self-update release
check (`updates.api_url`). Without a guard, an admin (or a compromised admin
session) can point these at `http://169.254.169.254/...` (cloud metadata),
`http://127.0.0.1:6379/` (internal Redis), etc. - classic SSRF.

`assert_public_http_url` resolves the host and rejects loopback, link-local
(incl. the 169.254.169.254 metadata range), multicast, reserved and
unspecified addresses **always**. Private LAN ranges (10/8, 172.16/12,
192.168/16, ULA) are rejected unless `allow_private=True` - which the OIDC
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


def assert_safe_host(host: str, port: int, *, allow_private: bool = True) -> None:
    """Same address policy as :func:`assert_public_http_url`, for outbound
    connections that are not URLs.

    The SMTP and IMAP "test connection" endpoints accept an inline host/port
    override, connect synchronously and hand the admin the resulting error
    text - a strictly stronger SSRF primitive than the webhook path, which is
    blind and re-validated per attempt, yet those two had no address check at
    all. `allow_private` defaults True because a mail server on the same LAN is
    an ordinary deployment; loopback, link-local (incl. the cloud-metadata
    address), multicast, reserved and unspecified are refused regardless.
    """
    host = (host or "").strip()
    if not host:
        raise AppError(400, "URL_NOT_ALLOWED", "No host given.")
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        # A host that does not resolve cannot be an SSRF target, and these are
        # "test your settings" endpoints whose whole job is to hand back a
        # legible connection error. Raising here would turn every typo into an
        # opaque 400 instead of the "connection failed" hint the admin needs -
        # so let it through and let the SMTP/IMAP layer report it.
        # `assert_public_http_url` deliberately keeps the opposite posture: it
        # validates a URL being STORED, where an unresolvable host is a
        # configuration error worth refusing up front.
        return
    _assert_addresses_allowed(infos, allow_private=allow_private)


def _assert_addresses_allowed(infos, *, allow_private: bool) -> None:
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        # Always blocked - never a legitimate IdP / release / mail host.
        if (
            ip.is_loopback
            or ip.is_link_local  # incl. 169.254.169.254 cloud-metadata
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise AppError(
                400, "URL_BLOCKED", "Host resolves to a non-routable address."
            )
        # Private LAN ranges: blocked unless explicitly permitted.
        if not allow_private and ip.is_private:
            raise AppError(
                400, "URL_BLOCKED", "Host resolves to a private address."
            )


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

    _assert_addresses_allowed(infos, allow_private=allow_private)
