"""Pure-Python formatters for share / file / time values.

Kept out of ``ui/`` so tests can import these without pulling PySide6
into the test process (see ``tests/conftest.py``)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def to_local(value: datetime) -> datetime:
    """Coerce a timestamp to the user's LOCAL timezone for display.

    The backend stores + serialises timestamps as naive UTC (finding C5).
    Rendering them verbatim showed a non-UTC user a wall-clock time offset
    from their own, with no tz label. Treat a naive value as UTC, then
    convert to local; an already-aware value is just converted."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone()


def format_datetime(value: datetime) -> str:
    """Render a non-nullable timestamp (e.g. created_at) in local time."""
    return to_local(value).strftime("%Y-%m-%d %H:%M")


def format_expiry(value: Optional[datetime]) -> str:
    """Render a share's ``expires_at`` (in local time) for the detail
    dialog + list view.

    ``None`` is intentional in the backend (v1.1.4 "Never" preset) —
    render it as the word ``"Never"`` rather than an em-dash or empty
    string so the row reads as a deliberate choice, not missing data."""
    if value is None:
        return "Never"
    return to_local(value).strftime("%Y-%m-%d %H:%M")
