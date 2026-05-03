"""User-agent fingerprint hash. Used for known_device tracking.

Strips patch-version digits so 'Chrome/138.0.7236.50' and 'Chrome/138.0.7236.62'
hash to the same value. Phase 7's new-device alert uses (UA-fingerprint, IP-
geohash) as the device identifier; without this normalization, every Chrome
auto-update would fire an alert.
"""
from __future__ import annotations

import hashlib
import re

_VERSION_TRIM = re.compile(r"(\d+\.\d+\.\d+)\.\d+")


def ua_fingerprint_hash(ua: str | None) -> str:
    if not ua:
        return ""
    stripped = _VERSION_TRIM.sub(r"\1", ua)
    return hashlib.sha256(stripped.encode("utf-8")).hexdigest()[:32]
