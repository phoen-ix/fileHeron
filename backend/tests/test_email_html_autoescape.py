"""Regression: HTML email templates must autoescape user-controlled fields.

Audit finding M1 - the Jinja env used `select_autoescape(["html"])`, but
templates end in `.html.j2`, so autoescape silently never fired and
user-controlled `subject`/`message`/`sender_name`/`filename` were injected
raw into HTML mail (phishing / webmail XSS). The env now matches `.html.j2`.
"""
from __future__ import annotations

from app.services.email import render_email


def test_share_created_html_escapes_subject_and_message():
    subject, text, html = render_email(
        "en",
        "share_created",
        {
            "sender_name": "<b>Mallory</b>",
            "subject": "<img src=x onerror=alert(1)>",
            "message": "<script>alert('xss')</script>",
            "file_count": 1,
            "share_url": "https://example.test/d/abc",
            "expires_at": None,
        },
    )
    assert html is not None
    # The raw injected markup must NOT appear verbatim in the HTML body.
    assert "<img src=x onerror=alert(1)>" not in html
    assert "<script>alert('xss')</script>" not in html
    # It must appear HTML-escaped instead.
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&lt;script&gt;" in html


def test_txt_template_is_not_escaped():
    # Plain-text bodies must stay raw (no &lt; entities for legitimate text).
    subject, text, html = render_email(
        "en",
        "share_created",
        {
            "sender_name": "Acme & Co",
            "subject": "Q1 < Q2 results",
            "message": "see <attached>",
            "file_count": 2,
            "share_url": "https://example.test/d/abc",
            "expires_at": None,
        },
    )
    assert "Acme & Co" in text
    assert "&amp;" not in text
