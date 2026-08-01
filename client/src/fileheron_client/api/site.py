"""The instance's public configuration.

Anonymous, and the only thing the client needs from it is `site_timezone`: the
SPA renders and INTERPRETS every wall-clock time in the instance's timezone
(`site.timezone`), and the client used the machine's local zone for both. A
travelling employee on America/New_York setting an expiry of "17:00" against a
Europe/Vienna instance sent 21:00Z, which the recipient's browser rendered as
23:00 - and neither client surface showed a zone, so the sender believed 17:00
and the recipient believed 23:00 (audit #2).
"""
from __future__ import annotations

from typing import Any

from .client import ApiClient


def public_config(api: ApiClient) -> dict[str, Any]:
    """`GET /api/config-public`. Never raises: an older server, or none at all,
    just means the client keeps its previous (local-timezone) behaviour."""
    try:
        return api.request_or_raise("GET", "/api/config-public") or {}
    except Exception:
        return {}
