"""OIDC endpoints must be HTTPS unless the operator explicitly opts out.

Every outbound OIDC call - discovery, JWKS and the token exchange - passed
`require_https=False`, so an `http://` issuer was accepted. That is worse than
"an ID token could be forged in transit": `_exchange_code` POSTs the provider's
CLIENT SECRET to the token endpoint, and the token endpoint is whatever the
discovery document says it is. A plaintext issuer therefore leaked the client
secret on every login, and a network attacker could rewrite discovery to
redirect that secret-bearing POST anywhere (audit 2026-07-30).

HTTPS is now the default. OIDC_ALLOW_INSECURE_HTTP exists only for a
self-hosted IdP on a trusted private network with no TLS, and is env-only so it
cannot be flipped from a compromised admin session.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.middleware.errors import AppError
from app.utils.net import assert_public_http_url


def test_https_is_the_default():
    assert settings.OIDC_ALLOW_INSECURE_HTTP is False, (
        "the insecure opt-out must default to off"
    )


def test_plain_http_is_rejected_when_https_is_required():
    with pytest.raises(AppError) as exc:
        assert_public_http_url(
            "http://idp.internal/.well-known/openid-configuration",
            allow_private=True,
            require_https=True,
        )
    assert exc.value.code in ("URL_NOT_ALLOWED", "URL_BLOCKED")


def test_https_is_accepted():
    """Control: the guard must not reject legitimate https IdPs. Uses a
    resolvable public host so only the scheme check is under test."""
    assert_public_http_url(
        "https://example.com/.well-known/openid-configuration",
        allow_private=True,
        require_https=True,
    )


def test_opt_out_restores_http():
    """The escape hatch must actually work, or operators on a private-network
    IdP have no path forward."""
    assert_public_http_url(
        "http://example.com/.well-known/openid-configuration",
        allow_private=True,
        require_https=False,
    )


@pytest.mark.parametrize(
    "module,attr",
    [
        ("app.services.oidc", None),
        ("app.services.jwks", None),
        ("app.routers.admin.oidc", None),
    ],
)
def test_no_call_site_hardcodes_insecure_http(module, attr):
    """The three modules must derive require_https from the setting rather than
    pinning it False - that hardcoding is exactly what the defect was."""
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module(module))
    assert "require_https=False" not in src, (
        f"{module} still hardcodes require_https=False"
    )
    if "assert_public_http_url" in src:
        assert "OIDC_ALLOW_INSECURE_HTTP" in src, (
            f"{module} calls the SSRF guard without consulting the opt-out"
        )
