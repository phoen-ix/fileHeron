"""Canonical UTC clock helpers.

Timestamps are stored as **naive UTC** (MariaDB DATETIME drops tz). This
module is the single source of truth for "now" so the ~50 ad-hoc `_utcnow`
redefinitions don't drift. JWT iat/exp need an AWARE value so `.timestamp()`
returns the correct epoch - use `utc_now_aware()` there.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import overload


def utc_now() -> datetime:
    """Naive UTC - the stored-timestamp convention across the app."""
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


@overload
def to_naive_utc(dt: datetime) -> datetime: ...


@overload
def to_naive_utc(dt: None) -> None: ...


def to_naive_utc(dt: datetime | None) -> datetime | None:
    """Normalise a client-supplied datetime to the storage convention.

    An offset-aware value is converted to UTC and stripped; a naive one is
    passed through as already-UTC. Query parameters that reach a `created_at`
    comparison MUST go through this: comparing an aware value against a naive
    DATETIME column is a silently wrong comparison, and accepting a bare
    wall-clock string as if it were UTC is the same bug from the other side
    (audit #2 - the admin log date filters)."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


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
