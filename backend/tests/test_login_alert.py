"""New-device login alert dispatch + UA suppression."""
from __future__ import annotations

import pytest

from app.services import login_alert as la_svc
from app.services.login_alert import _summarize_ua
from app.utils.ua_fingerprint import ua_fingerprint_hash


def test_ua_fingerprint_strips_patch_version():
    """Chrome auto-update bumps patch number; our fingerprint must
    survive the change so we don't email on every minor release."""
    ua_a = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.7236.50 Safari/537.36"
    ua_b = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.7236.62 Safari/537.36"
    assert ua_fingerprint_hash(ua_a) == ua_fingerprint_hash(ua_b)


def test_ua_fingerprint_changes_on_major_version_bump():
    a = "Mozilla/5.0 Chrome/138.0.0.0"
    b = "Mozilla/5.0 Chrome/139.0.0.0"
    assert ua_fingerprint_hash(a) != ua_fingerprint_hash(b)


def test_ua_summary_browsers():
    ua_chrome_mac = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/138.0.0.0"
    ua_firefox_win = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/120.0"
    ua_safari_ios = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit/605 Version/17.0 Mobile Safari/604.1"
    assert "Chrome" in _summarize_ua(ua_chrome_mac)
    assert "macOS" in _summarize_ua(ua_chrome_mac)
    assert "Firefox" in _summarize_ua(ua_firefox_win)
    assert "Windows" in _summarize_ua(ua_firefox_win)
    assert "Safari" in _summarize_ua(ua_safari_ios)
    assert "iOS" in _summarize_ua(ua_safari_ios)


def test_ua_summary_handles_empty():
    assert _summarize_ua("") == "unknown browser"


def test_fire_new_device_dispatches_login_alert(make_user, db, monkeypatch):
    """The alert helper goes through services/notification.dispatch
    with the login_alert category."""
    from unittest.mock import MagicMock
    from app.models.user import UserRole

    user = make_user(email="u@test.local", role=UserRole.client)
    captured = []
    monkeypatch.setattr(
        "app.services.login_alert.notif_svc.dispatch",
        lambda db, **kw: captured.append(kw),
    )

    fake_request = MagicMock()
    fake_request.client = MagicMock(host="203.0.113.42")
    fake_request.headers = {"user-agent": "Chrome/138.0.0.0"}

    la_svc.fire_new_device_alert(db, user=user, request=fake_request, via="password")
    assert len(captured) == 1
    assert captured[0]["category"].value == "login_alert"
    payload = captured[0]["payload"]
    assert payload["display_name"] == user.display_name
    assert payload["via"] == "password"
    assert "Chrome" in payload["ua_summary"]
