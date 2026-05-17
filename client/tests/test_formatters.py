"""Tests for the share-render helpers in ``fileheron_client.formatters``.

Kept here (not under ``tests/ui/``) deliberately — these helpers are
pure-Python and the conftest excludes the ``ui`` package + PySide6 from
the test process."""
from __future__ import annotations

from datetime import datetime

from fileheron_client.formatters import format_expiry


def test_format_expiry_renders_datetime_in_short_form():
    out = format_expiry(datetime(2026, 5, 17, 14, 30))
    assert out == "2026-05-17 14:30"


def test_format_expiry_renders_none_as_never_word():
    # v1.1.4 "Never" preset — backend sends expires_at: null. The list
    # + detail surfaces have to read as "deliberate no-expiry" rather
    # than "missing data".
    assert format_expiry(None) == "Never"
