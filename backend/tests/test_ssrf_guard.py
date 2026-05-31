"""Regression: assert_public_http_url blocks SSRF targets (finding M3)."""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.utils.net import assert_public_http_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:6379/",          # loopback / internal redis
        "http://localhost/",                # loopback by name
        "https://169.254.169.254/latest/", # cloud metadata (link-local)
        "http://[::1]:8000/",               # IPv6 loopback
        "http://0.0.0.0/",                  # unspecified
    ],
)
def test_blocks_non_routable(url):
    with pytest.raises(AppError) as exc:
        assert_public_http_url(url, allow_private=True, require_https=False)
    assert exc.value.status_code == 400


def test_blocks_private_when_not_allowed():
    with pytest.raises(AppError):
        assert_public_http_url("http://10.0.0.5/", allow_private=False, require_https=False)
    with pytest.raises(AppError):
        assert_public_http_url("http://192.168.1.10/", allow_private=False, require_https=False)


def test_allows_private_when_permitted():
    # Self-hosted IdP on a LAN — must be allowed under allow_private=True.
    assert_public_http_url("http://10.0.0.5/realms/fh", allow_private=True, require_https=False)
    assert_public_http_url("http://192.168.1.10:8080/", allow_private=True, require_https=False)


def test_requires_https_when_demanded():
    with pytest.raises(AppError):
        assert_public_http_url("http://example.com/", allow_private=False, require_https=True)


def test_allows_public_https():
    # api.github.com etc. — public, routable, https. Should not raise.
    assert_public_http_url("https://api.github.com/repos/x/y/releases", allow_private=False)
