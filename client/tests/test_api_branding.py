"""Branding logo fetch: bytes on 200, None on 404 / error (best-effort)."""
from __future__ import annotations

import httpx
import respx

from fileheron_client.api import ApiClient
from fileheron_client.api import branding as branding_api

SERVER = "https://files.example.com"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@respx.mock
def test_returns_bytes_on_200():
    respx.get(f"{SERVER}/api/branding/logo.png").mock(
        return_value=httpx.Response(200, content=_PNG, headers={"content-type": "image/png"})
    )
    api = ApiClient(SERVER)
    assert branding_api.branding_logo_png(api) == _PNG


@respx.mock
def test_returns_none_on_404():
    respx.get(f"{SERVER}/api/branding/logo.png").mock(return_value=httpx.Response(404))
    api = ApiClient(SERVER)
    assert branding_api.branding_logo_png(api) is None


@respx.mock
def test_returns_none_on_transport_error():
    respx.get(f"{SERVER}/api/branding/logo.png").mock(
        side_effect=httpx.ConnectError("boom")
    )
    api = ApiClient(SERVER)
    assert branding_api.branding_logo_png(api) is None
