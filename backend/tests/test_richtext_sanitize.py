"""Shared HTML sanitiser (services/richtext) + email alignment inlining (v1.50)."""
from __future__ import annotations

import pytest

from app.models.email_template_override import EmailTemplateOverride
from app.services import email as email_svc
from app.services import richtext
from app.utils.timeutil import utc_now


def test_alignment_classes_value_filtered():
    assert 'class="text-center"' in richtext.sanitize_html('<p class="text-center">x</p>')
    out = richtext.sanitize_html('<p class="evil text-right secret">x</p>')
    assert "text-right" in out
    assert "evil" not in out and "secret" not in out
    # A non-alignment class alone drops the attribute entirely.
    assert "class" not in richtext.sanitize_html('<p class="js-hook">x</p>')


def test_dangerous_markup_stripped():
    assert "<script>" not in richtext.sanitize_html("<p>x</p><script>alert(1)</script>")
    assert "onerror" not in richtext.sanitize_html('<img src="https://x/a.png" onerror="x" alt="a">')
    assert "javascript" not in richtext.sanitize_html('<img src="javascript:alert(1)">')
    assert "data:" not in richtext.sanitize_html('<img src="data:image/png;base64,AAAA">')
    # No inline style is ever allowed (alignment is class-based).
    assert "style" not in richtext.sanitize_html('<p style="text-align:center">x</p>')


def test_tables_images_marks_kept():
    out = richtext.sanitize_html(
        '<table><tr><td>a</td></tr></table><u>u</u><s>s</s>'
        '<img src="https://x/y.png" alt="ok">'
    )
    assert "<table>" in out and "<td>a</td>" in out
    assert "<u>u</u>" in out and "<s>s</s>" in out
    assert '<img src="https://x/y.png" alt="ok">' in out


def test_links_get_rel():
    out = richtext.sanitize_html('<a href="https://x">l</a>')
    assert 'href="https://x"' in out
    assert 'rel="noopener noreferrer nofollow"' in out


def test_render_markdown_safe_for_migration():
    out = richtext.render_markdown_safe("# Hi\n\nsome **body**")
    assert "<h1>Hi</h1>" in out
    assert "<strong>body</strong>" in out


def test_email_inline_alignment_helper():
    assert email_svc._inline_alignment('<p class="text-center">x</p>') == (
        '<p class="text-center" style="text-align:center">x</p>'
    )
    # Untouched when there's no alignment class.
    assert email_svc._inline_alignment("<p>x</p>") == "<p>x</p>"


@pytest.mark.asyncio
async def test_email_render_inlines_alignment_and_sanitizes(db):
    db.add(
        EmailTemplateOverride(
            slug="share_created", locale="en", subject=None,
            body_html='<p class="text-center">Hi [RECIPIENT]</p>'
                      '<script>alert(1)</script>',
            body_markdown="", updated_at=utc_now(),
        )
    )
    db.commit()
    _, text, html = email_svc.render_email(
        "en", "share_created",
        {"recipient_name": "Grace", "share_url": "https://x"}, db=db,
    )
    assert 'style="text-align:center"' in html  # alignment survives for mail clients
    assert "<script>" not in html
    assert "Hi Grace" in html
    assert "Hi Grace" in text  # plain-text alternative derived from the HTML
