"""Branding logo fetch for the desktop client.

The server serves a header-sized PNG rendition at ``/api/branding/logo.png``,
gated by the admin "Desktop client" toggle. The endpoint returns 404 when the
toggle is off or no logo is set, so the client only needs a 200/404 check.
"""
from __future__ import annotations

from typing import Optional

from .client import ApiClient


def branding_logo_png(api: ApiClient) -> Optional[bytes]:
    """Return the client logo PNG bytes, or None when none is available (404)
    or the request fails. Never raises - branding is best-effort decoration."""
    try:
        resp = api.request(
            "GET", "/api/branding/logo.png", retry_on_401=False
        )
    except Exception:
        return None
    if resp.status_code == 200 and resp.content:
        return resp.content
    return None
