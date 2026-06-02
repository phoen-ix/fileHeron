"""Pure-Python formatters for share / file / time values.

Kept out of ``ui/`` so tests can import these without pulling PySide6
into the test process (see ``tests/conftest.py``)."""
from __future__ import annotations

import time
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


# --- Download rate / ETA (per-file progress readout) ------------------------


def format_rate(bytes_per_sec: float) -> str:
    """Human-readable transfer rate, e.g. ``"12.3 MB/s"``. Own size logic so
    this module stays free of the ui/widgets (tkinter) dependency."""
    n = max(0.0, float(bytes_per_sec))
    units = ("B", "KB", "MB", "GB", "TB")
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.0f} {units[i]}/s" if i == 0 else f"{n:.1f} {units[i]}/s"


def format_eta(seconds: Optional[float]) -> str:
    """``"0:42"`` / ``"1:23:45"``; em-dash when unknown or non-positive."""
    if seconds is None or seconds <= 0 or seconds != seconds:  # None / <=0 / NaN
        return "—"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


class RateEstimator:
    """Smoothed transfer-rate + ETA from periodic ``(done, total)`` samples.

    Progress callbacks are coalesced to a few per second, so a raw delta would
    read jittery; an exponential moving average over a small sampling window
    keeps the displayed rate steady. ``now`` is injectable for tests."""

    def __init__(self, *, window: float = 0.3, alpha: float = 0.3) -> None:
        self._window = window
        self._alpha = alpha
        self._t0: Optional[float] = None
        self._last_t: Optional[float] = None
        self._last_done = 0
        self._ema: Optional[float] = None

    def update(
        self, done: int, total: int, *, now: Optional[float] = None
    ) -> tuple[float, Optional[float]]:
        """Return ``(rate_bytes_per_sec, eta_seconds_or_None)``."""
        t = time.monotonic() if now is None else now
        if self._t0 is None:
            self._t0 = t
            self._last_t = t
            self._last_done = done
        dt = t - (self._last_t if self._last_t is not None else t)
        if dt >= self._window:
            inst = max(0.0, (done - self._last_done) / dt)
            self._ema = inst if self._ema is None else (
                self._alpha * inst + (1 - self._alpha) * self._ema
            )
            self._last_t = t
            self._last_done = done
        rate = self._ema
        if rate is None:
            elapsed = t - self._t0
            rate = (done / elapsed) if elapsed > 0 else 0.0
        eta: Optional[float] = None
        if rate > 0 and total > 0 and done < total:
            eta = (total - done) / rate
        return rate, eta
