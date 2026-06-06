"""Override-aware email rendering (v1.25.0) — precedence, fallback, escaping,
sanitization, auth-link masking."""
from __future__ import annotations

from app.models.email_template_override import EmailTemplateOverride
from app.models.user import Locale
from app.services import email as email_svc
from app.services import email_placeholders as ep
from app.services import mail_log
from app.utils.timeutil import utc_now


def _add(db, slug, locale, body, subject=None):
    db.add(
        EmailTemplateOverride(
            slug=slug, locale=locale, subject=subject,
            body_markdown=body, updated_at=utc_now(),
        )
    )
    db.commit()


def test_override_takes_precedence(db):
    _add(db, "share_created", "en", "CUSTOM body for [RECIPIENT].", subject="Custom [SENDER]")
    subject, text, html = email_svc.render_email(
        "en", "share_created",
        {"recipient_name": "Grace", "sender_name": "Ada", "share_url": "https://x/s/1"},
        db=db,
    )
    assert subject == "Custom Ada"
    assert "CUSTOM body for Grace." in text
    # HTML is wrapped in the branded layout.
    assert "CUSTOM body for Grace." in html
    assert "Heron" in html


def test_no_override_matches_filesystem(db):
    ctx = {"recipient_name": "Grace", "sender_name": "Ada", "file_count": 2, "share_url": "https://x/s/1"}
    with_db = email_svc.render_email("en", "share_created", ctx, db=db)
    no_db = email_svc.render_email("en", "share_created", ctx)
    assert with_db == no_db
    # And it's the built-in subject, not a custom one.
    assert with_db[0].startswith("Ada sent you files")


def test_locale_falls_back_to_en_override(db):
    _add(db, "share_approved", "en", "EN override for [RECIPIENT].")
    # No DE row → falls back to the EN override.
    subject, text, html = email_svc.render_email(
        "de", "share_approved", {"recipient_name": "Grace", "share_url": "https://x/s/1"}, db=db
    )
    assert "EN override for Grace." in text


def test_dynamic_values_are_escaped_in_html_not_text(db):
    _add(db, "share_created", "en", "Hi [RECIPIENT], note: [MESSAGE]")
    subject, text, html = email_svc.render_email(
        "en", "share_created",
        {"recipient_name": "Grace", "message": "<script>alert(1)</script>", "share_url": "https://x"},
        db=db,
    )
    assert "<script>alert(1)</script>" in text  # raw in plain text
    assert "<script>" not in html  # never live in HTML
    assert "&lt;script&gt;" in html


def test_sanitize_html_strips_script_and_handlers():
    # nh3 is the defense-in-depth gate on generated HTML.
    out = email_svc._sanitize_html(
        '<p onclick="x()">hi</p><script>bad()</script>'
        '<a href="javascript:alert(1)">x</a>'
    )
    assert "<script>" not in out
    assert "onclick" not in out
    assert "javascript:" not in out


def test_raw_html_in_markdown_is_inert(db):
    # markdown-it has raw HTML disabled, so admin-typed tags render as escaped
    # text (no live tag / handler), and dangerous link schemes are stripped.
    _add(db, "share_created", "en", "Body\n\n<img src=x onerror=alert(1)>\n\n[x](javascript:alert(1))")
    _, _, html = email_svc.render_email(
        "en", "share_created", {"recipient_name": "G", "share_url": "https://x"}, db=db
    )
    assert "<img" not in html  # no live img tag
    assert "javascript:" not in html


def test_auth_link_canonical_path_preserved_and_masked(db):
    _add(db, "reset_password", "en", "Reset here: [reset]([RESET_LINK])")
    subject, text, html = email_svc.render_email(
        "en", "reset_password",
        {"display_name": "Grace", "reset_url": "https://x/reset-password/SECRET123"},
        db=db,
    )
    assert "/reset-password/SECRET123" in html
    masked_html, redacted_h = mail_log.mask_sensitive(html)
    masked_text, redacted_t = mail_log.mask_sensitive(text)
    assert redacted_h and "SECRET123" not in masked_html
    assert redacted_t and "SECRET123" not in masked_text


def test_editable_locales_track_locale_enum():
    # The editor's locale set must be Locale-derived (future-locale requirement).
    assert email_svc._LOCALE_CODES == {loc.value for loc in Locale}


def test_auth_link_placeholders_match_masking_regex():
    # Every auth_link placeholder's sample value must still match the mail-log
    # masking regex, so a customized auth email never leaks a live token.
    for slug, spec in ep.REGISTRY.items():
        ctx = ep.sample_ctx(slug, app_url="https://x.test")
        for p in spec.placeholders:
            if p.auth_link:
                val = ctx[p.context_key]
                assert mail_log._AUTH_LINK_RE.search(val), (slug, p.token, val)
