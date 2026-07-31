"""Canonical UTC clock helpers.

Timestamps are stored as **naive UTC** (MariaDB DATETIME drops tz). This
module is the single source of truth for "now" so the ~50 ad-hoc `_utcnow`
redefinitions don't drift. JWT iat/exp need an AWARE value so `.timestamp()`
returns the correct epoch - use `utc_now_aware()` there.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Naive UTC - the stored-timestamp convention across the app."""
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def utc_now_aware() -> datetime:
    """Timezone-aware UTC - for JWT iat/exp where epoch math matters."""
    return datetime.now(tz=timezone.utc)


def to_epoch(value: datetime) -> float:
    """Epoch seconds for a stored (naive UTC) timestamp.

    `datetime.timestamp()` on a naive value interprets it as LOCAL time and
    silently shifts it by the container's offset - the bug that bit the
    public-link unlock cookie. Stamp the tz first, always through here."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()
