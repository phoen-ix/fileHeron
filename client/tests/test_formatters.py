"""Tests for the share-render helpers in ``fileheron_client.formatters``.

Kept here (not under ``tests/ui/``) deliberately — these helpers are
pure-Python and the conftest excludes the ``ui`` package from the test
process."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from fileheron_client.formatters import (
    RateEstimator,
    format_datetime,
    format_eta,
    format_expiry,
    format_rate,
    to_local,
)

# time.tzset() is POSIX-only; the release CI runs on Windows. The exact
# local-string assertions need a pinned zone (tzset), so gate them; the
# instant-preservation tests below are cross-platform and cover the core
# invariant everywhere.
_HAS_TZSET = hasattr(time, "tzset")


# ---- cross-platform invariants (run everywhere) ----------------------------


def test_to_local_treats_naive_as_utc_preserving_instant():
    out = to_local(datetime(2026, 5, 17, 14, 30))
    assert out.tzinfo is not None  # result is tz-aware (local)
    # The absolute instant is "14:30 UTC" regardless of the local zone.
    assert out.astimezone(timezone.utc) == datetime(2026, 5, 17, 14, 30, tzinfo=timezone.utc)


def test_to_local_aware_preserves_instant():
    aware = datetime(2026, 5, 17, 14, 30, tzinfo=timezone.utc)
    assert to_local(aware).astimezone(timezone.utc) == aware


def test_format_expiry_renders_none_as_never_word():
    # v1.1.4 "Never" preset — backend sends expires_at: null.
    assert format_expiry(None) == "Never"


# ---- exact local rendering (POSIX only — needs a pinned TZ) -----------------


@pytest.fixture
def tz(monkeypatch):
    def _set(name: str):
        monkeypatch.setenv("TZ", name)
        time.tzset()
    yield _set
    time.tzset()  # restore the real zone after monkeypatch resets TZ


@pytest.mark.skipif(not _HAS_TZSET, reason="time.tzset is POSIX-only")
def test_format_expiry_naive_is_treated_as_utc(tz):
    tz("UTC")
    assert format_expiry(datetime(2026, 5, 17, 14, 30)) == "2026-05-17 14:30"


@pytest.mark.skipif(not _HAS_TZSET, reason="time.tzset is POSIX-only")
def test_format_expiry_converts_to_local(tz):
    tz("Etc/GMT-2")  # POSIX sign inversion → UTC+2
    assert format_expiry(datetime(2026, 5, 17, 14, 30)) == "2026-05-17 16:30"


@pytest.mark.skipif(not _HAS_TZSET, reason="time.tzset is POSIX-only")
def test_format_datetime_local(tz):
    tz("Etc/GMT-2")
    assert format_datetime(datetime(2026, 5, 17, 14, 30)) == "2026-05-17 16:30"


# ---- download rate / ETA ---------------------------------------------------


def test_format_rate_units():
    assert format_rate(0) == "0 B/s"
    assert format_rate(512) == "512 B/s"
    assert format_rate(1024) == "1.0 KB/s"
    assert format_rate(1536) == "1.5 KB/s"
    assert format_rate(12.3 * 1024 * 1024) == "12.3 MB/s"
    assert format_rate(3 * 1024**3) == "3.0 GB/s"
    assert format_rate(-5) == "0 B/s"  # clamped


def test_format_eta():
    assert format_eta(None) == "—"
    assert format_eta(0) == "—"
    assert format_eta(-3) == "—"
    assert format_eta(42) == "0:42"
    assert format_eta(83) == "1:23"
    assert format_eta(3 * 3600 + 23 * 60 + 45) == "3:23:45"


def test_rate_estimator_steady_rate_and_eta():
    est = RateEstimator(window=0.3, alpha=0.5)
    total = 10_000_000
    # First sample seeds t0; rate from elapsed until the window passes.
    est.update(0, total, now=0.0)
    # 1 MB/s: 1_000_000 bytes after 1.0s.
    rate, eta = est.update(1_000_000, total, now=1.0)
    assert 0.8e6 <= rate <= 1.2e6
    assert eta is not None and 8 <= eta <= 11  # ~9s remaining at ~1MB/s
    # Another second, another 1 MB → still ~1 MB/s.
    rate2, eta2 = est.update(2_000_000, total, now=2.0)
    assert 0.8e6 <= rate2 <= 1.2e6
    assert eta2 is not None and eta2 < eta  # ETA decreases as we progress


def test_rate_estimator_eta_none_when_complete_or_unknown_total():
    est = RateEstimator()
    est.update(0, 0, now=0.0)
    _, eta = est.update(500, 0, now=1.0)  # unknown total
    assert eta is None
    est2 = RateEstimator()
    est2.update(0, 100, now=0.0)
    _, eta2 = est2.update(100, 100, now=1.0)  # complete
    assert eta2 is None
