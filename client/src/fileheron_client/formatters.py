"""Pure-Python formatters for share / file / time values.

Kept out of ``ui/`` so tests can import these without pulling PySide6
into the test process (see ``tests/conftest.py``)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional


def format_expiry(value: Optional[datetime]) -> str:
    """Render a share's ``expires_at`` for the detail dialog + list view.

    ``None`` is intentional in the backend (v1.1.4 "Never" preset) —
    render it as the word ``"Never"`` rather than an em-dash or empty
    string so the row reads as a deliberate choice, not missing data."""
    if value is None:
        return "Never"
    return value.strftime("%Y-%m-%d %H:%M")
