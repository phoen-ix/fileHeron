"""Tests for the share-render helpers in ``fileheron_client.formatters``.

Kept here (not under ``tests/ui/``) deliberately — these helpers are
pure-Python and the conftest excludes the ``ui`` package from the test
process."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from fileheron_client.formatters import format_datetime, format_expiry


@pytest.fixture
def tz(monkeypatch):
    """Pin the process timezone so local-time formatting is deterministic.
    Linux-only (time.tzset); the CI + dev host are Linux."""
    def _set(name: str):
        monkeypatch.setenv("TZ", name)
        time.tzset()
    yield _set
    # monkeypatch restores TZ; re-apply so later tests see the real zone.
    time.tzset()


def test_format_expiry_naive_is_treated_as_utc(tz):
    tz("UTC")
    # Backend sends naive UTC; in a UTC locale it renders unchanged.
    assert format_expiry(datetime(2026, 5, 17, 14, 30)) == "2026-05-17 14:30"


def test_format_expiry_converts_to_local(tz):
    tz("Etc/GMT-2")  # POSIX sign inversion → UTC+2
    # 14:30 UTC shown as 16:30 local.
    assert format_expiry(datetime(2026, 5, 17, 14, 30)) == "2026-05-17 16:30"


def test_format_expiry_aware_datetime_converted(tz):
    tz("Etc/GMT-2")
    aware = datetime(2026, 5, 17, 14, 30, tzinfo=timezone.utc)
    assert format_expiry(aware) == "2026-05-17 16:30"


def test_format_datetime_local(tz):
    tz("Etc/GMT-2")
    assert format_datetime(datetime(2026, 5, 17, 14, 30)) == "2026-05-17 16:30"


def test_format_expiry_renders_none_as_never_word():
    # v1.1.4 "Never" preset — backend sends expires_at: null.
    assert format_expiry(None) == "Never"
