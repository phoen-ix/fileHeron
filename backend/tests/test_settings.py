"""Generic kv store: plain + encrypted roundtrip, get_bool parsing,
audit-row shape on settings_changed."""
from __future__ import annotations

from app.models.app_setting import AppSetting
from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole
from app.services import settings as settings_svc


def test_get_returns_none_when_unset(db):
    assert settings_svc.get(db, "nothing.here") is None


def test_set_then_get_plain_value(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    settings_svc.set_value(
        db, key=settings_svc.Keys.HOME_PAGE_ENABLED, value="true", actor=admin
    )
    db.commit()

    assert settings_svc.get(db, settings_svc.Keys.HOME_PAGE_ENABLED) == "true"

    row = (
        db.query(AppSetting)
        .filter(AppSetting.key == settings_svc.Keys.HOME_PAGE_ENABLED)
        .one()
    )
    assert row.is_encrypted is False
    assert row.value == "true"
    assert row.updated_by_id == admin.id


def test_set_then_get_encrypted_value(make_user, db):
    """SMTP_PASSWORD is in _ENCRYPTED_KEYS - value is Fernet ciphertext on disk
    but get() returns the plaintext."""
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    secret = "super-secret-smtp-password-42"

    settings_svc.set_value(
        db, key=settings_svc.Keys.SMTP_PASSWORD, value=secret, actor=admin
    )
    db.commit()

    assert settings_svc.get(db, settings_svc.Keys.SMTP_PASSWORD) == secret

    row = (
        db.query(AppSetting)
        .filter(AppSetting.key == settings_svc.Keys.SMTP_PASSWORD)
        .one()
    )
    assert row.is_encrypted is True
    # Ciphertext != plaintext.
    assert row.value != secret


def test_set_value_none_deletes_row(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    settings_svc.set_value(
        db, key=settings_svc.Keys.HOME_PAGE_ENABLED, value="true", actor=admin
    )
    db.commit()
    assert (
        db.query(AppSetting)
        .filter(AppSetting.key == settings_svc.Keys.HOME_PAGE_ENABLED)
        .count()
        == 1
    )

    settings_svc.set_value(
        db, key=settings_svc.Keys.HOME_PAGE_ENABLED, value=None, actor=admin
    )
    db.commit()
    assert (
        db.query(AppSetting)
        .filter(AppSetting.key == settings_svc.Keys.HOME_PAGE_ENABLED)
        .count()
        == 0
    )
    assert settings_svc.get(db, settings_svc.Keys.HOME_PAGE_ENABLED) is None


def test_set_value_overwrites_existing(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    settings_svc.set_value(
        db, key=settings_svc.Keys.SITE_URL, value="https://one.example", actor=admin
    )
    settings_svc.set_value(
        db, key=settings_svc.Keys.SITE_URL, value="https://two.example", actor=admin
    )
    db.commit()

    assert settings_svc.get(db, settings_svc.Keys.SITE_URL) == "https://two.example"
    assert (
        db.query(AppSetting)
        .filter(AppSetting.key == settings_svc.Keys.SITE_URL)
        .count()
        == 1
    )


def test_get_bool_parses_truthy_falsy(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    for raw in ("true", "1", "yes", "on", "True", "YES"):
        settings_svc.set_value(
            db, key=settings_svc.Keys.HOME_PAGE_ENABLED, value=raw, actor=admin
        )
        db.commit()
        assert settings_svc.get_bool(db, settings_svc.Keys.HOME_PAGE_ENABLED) is True, raw

    for raw in ("false", "0", "no", "off", "False"):
        settings_svc.set_value(
            db, key=settings_svc.Keys.HOME_PAGE_ENABLED, value=raw, actor=admin
        )
        db.commit()
        assert settings_svc.get_bool(db, settings_svc.Keys.HOME_PAGE_ENABLED) is False, raw


def test_get_bool_returns_default_on_unparseable(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    settings_svc.set_value(
        db, key=settings_svc.Keys.HOME_PAGE_ENABLED, value="maybe", actor=admin
    )
    db.commit()

    assert settings_svc.get_bool(db, settings_svc.Keys.HOME_PAGE_ENABLED, default=True) is True
    assert settings_svc.get_bool(db, settings_svc.Keys.HOME_PAGE_ENABLED, default=False) is False


def test_get_bool_returns_default_when_missing(db):
    assert settings_svc.get_bool(db, "missing.key", default=True) is True
    assert settings_svc.get_bool(db, "missing.key", default=False) is False


def test_audit_settings_change_records_sorted_keys(make_user, db):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    settings_svc.audit_settings_change(
        db,
        actor=admin,
        changed_keys=["smtp.host", "smtp.port", "smtp.host"],  # dup intentional
    )
    db.commit()

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.settings_changed.value)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].actor_user_id == admin.id
    # Dedup + sort.
    assert rows[0].extra["keys"] == ["smtp.host", "smtp.port"]


def test_decryption_failure_returns_none(make_user, db):
    """If JWT_SECRET rotates and an encrypted row can't be decrypted, get()
    falls back to None rather than crashing - env fallback then wins."""
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    settings_svc.set_value(
        db, key=settings_svc.Keys.SMTP_PASSWORD, value="real-secret", actor=admin
    )
    db.commit()

    row = (
        db.query(AppSetting)
        .filter(AppSetting.key == settings_svc.Keys.SMTP_PASSWORD)
        .one()
    )
    row.value = "not-a-fernet-token"
    db.commit()

    assert settings_svc.get(db, settings_svc.Keys.SMTP_PASSWORD) is None
