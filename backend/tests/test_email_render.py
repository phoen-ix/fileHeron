"""Email rendering — locale resolution + subject lookup + dt_locale filter."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import email as email_svc


def _utc(year, month, day, hour=12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc).replace(tzinfo=None)


def test_render_email_en_share_created_returns_subject_text_html():
    payload = {
        "sender_name": "Alice",
        "subject": "Q3 reports",
        "message": "Here you go.",
        "expires_at": _utc(2026, 5, 5),
        "file_count": 2,
        "share_url": "https://example.com/share/abc",
        "recipient_name": "Bob",
    }
    subject, text, html = email_svc.render_email("en", "share_created", payload)
    assert "Alice" in subject
    assert "Q3 reports" in text
    assert "Q3 reports" in html
    assert "Open the share" in html


def test_render_email_de_locale_uses_de_template():
    payload = {
        "sender_name": "Alice",
        "subject": "Q3 reports",
        "message": None,
        "expires_at": _utc(2026, 5, 5),
        "file_count": 1,
        "share_url": "https://example.com/share/abc",
        "recipient_name": "Bob",
    }
    subject, text, _html = email_svc.render_email("de", "share_created", payload)
    # Subject template is the DE one.
    assert "gesendet" in subject.lower()
    assert "Hallo" in text


def test_unknown_locale_falls_back_to_en():
    payload = {
        "sender_name": "Alice",
        "subject": "x",
        "message": None,
        "expires_at": _utc(2026, 5, 5),
        "file_count": 1,
        "share_url": "https://example.com/share/abc",
        "recipient_name": "Bob",
    }
    subject, text, _html = email_svc.render_email("xx", "share_created", payload)
    assert "sent you" in subject
    assert "Hi" in text


def test_dt_locale_filter_includes_utc_marker():
    formatted = email_svc._format_dt_locale(_utc(2026, 5, 3, 9), "en")
    assert "(UTC)" in formatted
    assert "May" in formatted


def test_subjects_book_loaded_for_both_locales():
    assert "share_created" in email_svc._SUBJECTS["en"]
    assert "share_created" in email_svc._SUBJECTS["de"]


def test_render_email_no_html_falls_back_gracefully(tmp_path, monkeypatch):
    # If the template lookup throws (e.g. missing html.j2), html should
    # come back as None — text path remains required.
    payload = {
        "display_name": "Bob",
        "verify_url": "https://example.com/verify/x",
    }
    subject, text, html = email_svc.render_email("en", "verify", payload)
    assert subject == "Verify your email"
    assert "verify" in text.lower()
    # The verify template only exists as txt — no html companion.
    assert html is None


@pytest.mark.parametrize(
    "slug, locale, expect_in_subject",
    [
        ("share_created", "en", "sent you"),
        ("share_created", "de", "gesendet"),
        ("share_expiring", "en", "expires in 24 hours"),
        ("share_expiring", "de", "läuft in 24 Stunden"),
        ("public_link_downloaded", "en", "public link"),
        ("public_link_downloaded", "de", "öffentlicher Link"),
        ("account_created", "en", "joined"),
        ("account_created", "de", "beigetreten"),
        ("file_quarantined", "en", "infected"),
        ("file_quarantined", "de", "Quarantäne"),
    ],
)
def test_subjects_render_per_locale(slug, locale, expect_in_subject):
    # Pre-fill enough payload keys to satisfy str.format() in subjects.
    common = {
        "sender_name": "Alice",
        "invitee_name": "Bob",
    }
    subject = email_svc._resolve_subject(locale, slug, common)
    assert expect_in_subject.lower() in subject.lower()
