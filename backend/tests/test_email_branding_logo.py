"""The email header logo appears only when enabled + a logo is stored."""
from __future__ import annotations

from app.services import email as email_svc
from app.services import settings as settings_svc


def _payload():
    return {
        "sender_name": "Alice", "subject": "Hi", "message": None,
        "expires_at": None, "file_count": 1,
        "share_url": "https://example.com/x", "recipient_name": "Bob",
    }


def test_no_logo_by_default(make_user, db):
    _subject, _text, html = email_svc.render_email(
        "en", "share_created", _payload(), app_url="https://fh.example", db=db,
    )
    assert html and "/api/branding/logo" not in html


def test_logo_when_enabled(make_user, db):
    admin = make_user(email="a@test.local")
    settings_svc.set_value(db, key=settings_svc.Keys.BRANDING_SHOW_EMAIL, value="true", actor=admin)
    settings_svc.set_value(db, key=settings_svc.Keys.BRANDING_LOGO_LOCATOR, value="/data/files/x.bin", actor=admin)
    db.commit()
    _subject, _text, html = email_svc.render_email(
        "en", "share_created", _payload(), app_url="https://fh.example", db=db,
    )
    assert html and 'src="https://fh.example/api/branding/logo"' in html


def test_logo_suppressed_when_surface_off(make_user, db):
    admin = make_user(email="a@test.local")
    # Logo stored but the email surface is off -> no img.
    settings_svc.set_value(db, key=settings_svc.Keys.BRANDING_SHOW_EMAIL, value="false", actor=admin)
    settings_svc.set_value(db, key=settings_svc.Keys.BRANDING_LOGO_LOCATOR, value="/data/files/x.bin", actor=admin)
    db.commit()
    _subject, _text, html = email_svc.render_email(
        "en", "share_created", _payload(), app_url="https://fh.example", db=db,
    )
    assert html and "/api/branding/logo" not in html
