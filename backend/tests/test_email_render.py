"""Email rendering - locale resolution + subject lookup + dt_locale filter."""
from __future__ import annotations

import logging
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


def test_render_email_ops_alert_renders():
    # ops_alert had no template, so selecting the email channel silently failed.
    payload = {"reason": "cron_failed", "job_name": "expire_files", "error": "boom",
               "at": "2026-07-04T10:00:00"}
    subject, text, _html = email_svc.render_email("en", "ops_alert", payload)
    assert "operations alert" in subject.lower()
    assert "expire_files" in text and "boom" in text


def test_render_email_inbound_message_renders_de():
    payload = {"sender": "a@x.com", "subject": "Hi there", "classification": "normal"}
    subject, text, _html = email_svc.render_email("de", "inbound_message", payload)
    assert "nachricht" in subject.lower()
    assert "a@x.com" in text and "Hi there" in text


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
    # `_format_dt_locale` is a @pass_context Jinja filter - invoke it through
    # a rendered template so Jinja injects the rendering context itself.
    # Calling it directly is brittle: how pass_context threads the context
    # argument varies by Jinja version.
    tmpl = email_svc._env.from_string("{{ d | dt_locale('en') }}")
    formatted = tmpl.render(d=_utc(2026, 5, 3, 9), site_timezone="UTC")
    assert "(UTC)" in formatted
    assert "May" in formatted


def test_subjects_book_loaded_for_both_locales():
    assert "share_created" in email_svc._SUBJECTS["en"]
    assert "share_created" in email_svc._SUBJECTS["de"]


def test_render_email_share_files_added_en_de():
    payload = {
        "sender_name": "Alice",
        "subject": "Q3 reports",
        "added_count": 3,
        "share_url": "https://example.com/share/abc",
        "recipient_name": "Bob",
    }
    for locale in ("en", "de"):
        subject, text, html = email_svc.render_email(locale, "share_files_added", payload)
        assert "Alice" in subject
        assert "3" in text
        assert "Q3 reports" in html
        assert "https://example.com/share/abc" in html


def test_an_html_render_failure_degrades_to_text_and_says_so(monkeypatch, caplog):
    """A failing HTML render must still produce a sendable text-only mail.

    This used to be pinned via `verify`, which merely happened to ship no
    `.html.j2` - so it asserted a gap in the templates rather than the
    degradation contract it named. Every slug ships an HTML half now
    (tests/test_email_template_matrix.py), so the branch is exercised directly.

    The caplog half is the point: the branch is only defensible if it leaves a
    trace. It had none, which is why release_available.html.j2 raised on every
    send for its whole life and shipped text-only with nobody the wiser.
    """
    real = email_svc._render

    def _boom(locale, slug, kind, ctx, **kw):
        if kind == "html":
            raise RuntimeError("template exploded")
        return real(locale, slug, kind, ctx, **kw)

    monkeypatch.setattr(email_svc, "_render", _boom)
    payload = {
        "display_name": "Bob",
        "verify_url": "https://example.com/verify-email/x",
    }
    with caplog.at_level(logging.ERROR, logger="fileheron.email"):
        subject, text, html = email_svc.render_email("en", "verify", payload)

    assert subject == "Verify your email"
    assert "verify" in text.lower()
    assert html is None
    assert any(
        "template exploded" in r.getMessage() or r.exc_info for r in caplog.records
    ), "the html render failure left no trace anywhere"


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
