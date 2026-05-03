"""Privacy-friendly IP "neighborhood" hash. NOT real geolocation.

Used by Phase 7's new-device alert: hash IP /24 (v4) or /64 (v6) → 5 chars.
Two logins from the same /24 produce the same geohash, so a Chrome update on
the same network won't fire a "new device" alert. Two logins from genuinely
different networks differ.

Does not require maxminddb or any external DB.
"""
from __future__ import annotations

import hashlib


def ip_geohash5(ip: str | None) -> str:
    if not ip:
        return ""
    if "." in ip:
        parts = ip.split(".")
        prefix = ".".join(parts[:3]) if len(parts) >= 3 else ip
    else:
        parts = ip.split(":")
        prefix = ":".join(parts[:4]) if len(parts) >= 4 else ip
    return hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:5]
