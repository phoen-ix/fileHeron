"""Tests for the share-render helpers in ``fileheron_client.formatters``.

Kept here (not under ``tests/ui/``) deliberately - these helpers are
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
    # v1.1.4 "Never" preset - backend sends expires_at: null. Localised since
    # audit #2: this literal was the only thing pinning it to English, on a
    # client whose every other label is translated.
    from fileheron_client.i18n import set_locale, t

    set_locale("en")
    assert format_expiry(None) == t("common.never") == "Never"
    set_locale("de")
    assert format_expiry(None) == "Nie"
    set_locale("en")


# ---- exact rendering, on every platform -------------------------------------
#
# These pinned the zone with TZ + `time.tzset()`, which is POSIX-only, so they
# SKIPPED on the one platform this client ships for - three assertions about how
# a timestamp is rendered, never once evaluated on Windows. They pin the zone
# through `set_display_timezone` instead: the same mechanism the client itself
# uses, backed by `zoneinfo` and the bundled `tzdata`, and identical on both
# platforms. The TZ/tzset fixture is kept below for the one property that is
# genuinely about the machine's own zone.


@pytest.fixture
def display_tz():
    from fileheron_client.formatters import set_display_timezone

    def _set(name: str):
        set_display_timezone(name)

    yield _set
    set_display_timezone(None)


@pytest.fixture
def tz(monkeypatch):
    def _set(name: str):
        monkeypatch.setenv("TZ", name)
        time.tzset()
    yield _set
    time.tzset()  # restore the real zone after monkeypatch resets TZ


def test_format_expiry_naive_is_treated_as_utc(display_tz):
    display_tz("UTC")
    assert format_expiry(datetime(2026, 5, 17, 14, 30)) == "2026-05-17 14:30"


def test_format_expiry_converts_to_the_display_zone(display_tz):
    display_tz("Europe/Vienna")  # UTC+2 in May
    assert format_expiry(datetime(2026, 5, 17, 14, 30)) == "2026-05-17 16:30"


def test_format_datetime_uses_the_display_zone(display_tz):
    display_tz("Europe/Vienna")
    assert format_datetime(datetime(2026, 5, 17, 14, 30)) == "2026-05-17 16:30"


@pytest.mark.skipif(not _HAS_TZSET, reason="time.tzset is POSIX-only")
def test_with_no_display_zone_it_falls_back_to_the_machine(tz):
    """The fallback path, which IS about the machine's own zone - so this one
    legitimately needs tzset and legitimately skips on Windows."""
    from fileheron_client.formatters import set_display_timezone

    set_display_timezone(None)
    tz("Etc/GMT-2")  # POSIX sign inversion -> UTC+2
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
    assert format_eta(None) == "-"
    assert format_eta(0) == "-"
    assert format_eta(-3) == "-"
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


# --- audit #2: the instance's timezone, and localised backend errors --------


def test_times_render_in_the_instances_timezone():
    """The SPA renders AND interprets every wall-clock time in
    `site.timezone`; the client used the machine's local zone for both, so a
    laptop on America/New_York against a Europe/Vienna instance was six hours
    out in each direction - with no zone label on either client surface to
    reveal it."""
    from datetime import datetime, timezone

    from fileheron_client.formatters import (
        format_datetime,
        set_display_timezone,
        timezone_label,
    )

    instant = datetime(2026, 8, 5, 15, 0, tzinfo=timezone.utc)
    # Deliberately strict. On Windows `zoneinfo` has no database of its own, so
    # this silently rendered in the machine's local zone - the exact defect the
    # feature exists to fix, on the only platform the client ships for. The
    # `tzdata` dependency is what makes it pass; if that is ever dropped, this
    # is the test that says so (client-v1.3.1).
    try:
        set_display_timezone("Europe/Vienna")
        assert format_datetime(instant) == "2026-08-05 17:00"
        assert timezone_label() == "Europe/Vienna"

        set_display_timezone("America/New_York")
        assert format_datetime(instant) == "2026-08-05 11:00"
    finally:
        set_display_timezone(None)


def test_an_unknown_timezone_falls_back_to_local():
    """An older server sends no `site_timezone`, and a bad value must not throw
    on a background thread during sign-in."""
    from fileheron_client.formatters import display_timezone, set_display_timezone

    set_display_timezone("Not/AZone")
    assert display_timezone() is None
    set_display_timezone(None)
    assert display_timezone() is None


def test_a_backend_error_is_shown_in_the_users_language():
    """Every label around the user was translated and the error itself was
    not: `ApiError.message` is whatever the backend wrote, and the backend
    writes English."""
    from fileheron_client.api.client import ApiError
    from fileheron_client.i18n import set_locale

    err = ApiError(
        status_code=410,
        code="SHARE_NOT_ACTIVE",
        message="This share is no longer active.",
    )
    try:
        set_locale("de")
        localized = err.localized()
        assert localized != err.message
        assert localized.strip()
    finally:
        set_locale("en")


def test_an_unknown_code_falls_back_to_the_servers_text():
    """A newer server can emit a code this client build has never heard of;
    showing a raw key would be worse than showing English."""
    from fileheron_client.api.client import ApiError
    from fileheron_client.i18n import set_locale

    err = ApiError(status_code=418, code="A_CODE_FROM_THE_FUTURE", message="Teapot.")
    set_locale("de")
    try:
        assert err.localized() == "Teapot."
    finally:
        set_locale("en")
