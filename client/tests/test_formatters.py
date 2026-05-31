"""Tests for the share-render helpers in ``fileheron_client.formatters``.

Kept here (not under ``tests/ui/``) deliberately — these helpers are
pure-Python and the conftest excludes the ``ui`` package from the test
process."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from fileheron_client.formatters import format_datetime, format_expiry, to_local

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
