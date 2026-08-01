"""Pure-Python formatters for share / file / time values.

Kept out of ``ui/`` so tests can import these without pulling PySide6
into the test process (see ``tests/conftest.py``)."""
from __future__ import annotations

import time
from datetime import datetime, timezone, tzinfo
from typing import Optional

# The instance's timezone, once the client has read /api/config-public. The SPA
# renders AND interprets every wall-clock time in it, and the client used the
# machine's local zone for both - so a laptop on America/New_York against a
# Europe/Vienna instance was six hours out in each direction, with no zone label
# on either client surface to reveal it (audit #2).
_display_tz: Optional[tzinfo] = None
_display_tz_name: str = ""


def timezone_database_problem() -> Optional[str]:
    """None if a real IANA zone can be resolved, else why not.

    Windows ships no time-zone database, so `ZoneInfo` raises there unless the
    `tzdata` package is installed AND bundled into the .exe - and when it does,
    `set_display_timezone` falls back to local time, silently. That is the whole
    feature failing closed on the only platform this client runs on, and it
    shipped as client-v1.3.0.

    Lives HERE rather than in `__main__._selfcheck` so it can be executed by the
    test suite: importing `__main__` pulls in the GUI stack, which headless CI
    has no Tk for - which is precisely why the frozen-bundle check could not be
    exercised anywhere before the release runner.
    """
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo("Europe/Vienna")
    except Exception as exc:
        return (
            f"the IANA time-zone database is unavailable ({type(exc).__name__}: "
            f"{exc}) - every timestamp would render in this machine's local zone "
            "instead of the instance's"
        )
    return None


def set_display_timezone(name: Optional[str]) -> None:
    """Adopt the instance's timezone. Unknown or missing -> stay local."""
    global _display_tz, _display_tz_name
    if not name:
        _display_tz, _display_tz_name = None, ""
        return
    try:
        from zoneinfo import ZoneInfo

        _display_tz = ZoneInfo(name)
        _display_tz_name = name
    except Exception:
        _display_tz, _display_tz_name = None, ""


def display_timezone() -> Optional[tzinfo]:
    return _display_tz


def timezone_label() -> str:
    """The zone every rendered timestamp is in, for the UI to show beside
    them."""
    if _display_tz_name:
        return _display_tz_name
    return datetime.now().astimezone().strftime("%Z") or "local time"


def to_local(value: datetime) -> datetime:
    """Coerce a timestamp to the user's LOCAL timezone for display.

    The backend stores + serialises timestamps as naive UTC (finding C5).
    Rendering them verbatim showed a non-UTC user a wall-clock time offset
    from their own, with no tz label. Treat a naive value as UTC, then
    convert to local; an already-aware value is just converted."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(_display_tz) if _display_tz else aware.astimezone()


def format_datetime(value: datetime) -> str:
    """Render a non-nullable timestamp (e.g. created_at) in local time."""
    return to_local(value).strftime("%Y-%m-%d %H:%M")


def format_expiry(value: Optional[datetime]) -> str:
    """Render a share's ``expires_at`` (in local time) for the detail
    dialog + list view.

    ``None`` is intentional in the backend (v1.1.4 "Never" preset) -
    render it as the word ``"Never"`` rather than an em-dash or empty
    string so the row reads as a deliberate choice, not missing data."""
    if value is None:
        from .i18n import t as _t

        return _t("common.never")
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
        return "-"
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
