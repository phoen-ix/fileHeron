"""resolve_smtp_config(db) - DB-overlay-env matrix (post-Phase 10)."""
from __future__ import annotations

from app.services import email as email_svc
from app.services import settings as settings_svc


def test_env_only_when_db_empty(db, monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.SMTP_HOST", "smtp.env.example"
    )
    monkeypatch.setattr("app.config.settings.SMTP_PORT", 2525)
    monkeypatch.setattr("app.config.settings.SMTP_USER", "envuser")
    monkeypatch.setattr("app.config.settings.SMTP_PASSWORD", "envpass")
    monkeypatch.setattr(
        "app.config.settings.SMTP_FROM_EMAIL", "noreply@env.example"
    )
    monkeypatch.setattr("app.config.settings.SMTP_FROM_NAME", "EnvHeron")

    cfg = email_svc.resolve_smtp_config(db)
    assert cfg.host == "smtp.env.example"
    assert cfg.port == 2525
    assert cfg.user == "envuser"
    assert cfg.password == "envpass"
    assert cfg.from_email == "noreply@env.example"
    assert cfg.from_name == "EnvHeron"
    # No db row + port != 465 → defaults to 'starttls' for back-compat.
    assert cfg.tls_mode == "starttls"
    assert cfg.is_configured is True


def test_env_port_465_defaults_to_implicit_tls(db, monkeypatch):
    monkeypatch.setattr("app.config.settings.SMTP_HOST", "smtps.example")
    monkeypatch.setattr("app.config.settings.SMTP_PORT", 465)
    cfg = email_svc.resolve_smtp_config(db)
    assert cfg.tls_mode == "implicit"


def test_db_overrides_env(db, make_user, monkeypatch):
    monkeypatch.setattr("app.config.settings.SMTP_HOST", "smtp.env.example")
    monkeypatch.setattr("app.config.settings.SMTP_PORT", 587)
    admin = make_user(role="admin")
    settings_svc.set_value(
        db, key=settings_svc.Keys.SMTP_HOST, value="smtp.db.example", actor=admin
    )
    settings_svc.set_value(
        db, key=settings_svc.Keys.SMTP_PORT, value="2587", actor=admin
    )
    settings_svc.set_value(
        db, key=settings_svc.Keys.SMTP_TLS_MODE, value="none", actor=admin
    )
    db.commit()

    cfg = email_svc.resolve_smtp_config(db)
    assert cfg.host == "smtp.db.example"
    assert cfg.port == 2587
    assert cfg.tls_mode == "none"


def test_db_password_encrypted_round_trip(db, make_user):
    admin = make_user(role="admin")
    settings_svc.set_value(
        db, key=settings_svc.Keys.SMTP_PASSWORD, value="hunter2", actor=admin
    )
    db.commit()
    # Confirm round-trip; the row is encrypted at rest but the resolver
    # decrypts.
    cfg = email_svc.resolve_smtp_config(db)
    assert cfg.password == "hunter2"
    # The raw stored value is NOT the plaintext.
    from app.models.app_setting import AppSetting

    row = (
        db.query(AppSetting)
        .filter(AppSetting.key == settings_svc.Keys.SMTP_PASSWORD)
        .one()
    )
    assert row.is_encrypted is True
    assert "hunter2" not in row.value


def test_helo_hostname_env_and_db_override(db, make_user, monkeypatch):
    monkeypatch.setattr("app.config.settings.SMTP_HELO_HOST", "helo.env.example")
    # Env value flows through when no DB row is set.
    assert email_svc.resolve_smtp_config(db).helo_hostname == "helo.env.example"
    # DB override wins.
    admin = make_user(role="admin")
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.SMTP_HELO_HOSTNAME,
        value="helo.db.example",
        actor=admin,
    )
    db.commit()
    assert email_svc.resolve_smtp_config(db).helo_hostname == "helo.db.example"


def test_helo_hostname_defaults_empty(db, monkeypatch):
    monkeypatch.setattr("app.config.settings.SMTP_HELO_HOST", "")
    assert email_svc.resolve_smtp_config(db).helo_hostname == ""


def test_is_configured_flips_when_host_empty(db, monkeypatch):
    monkeypatch.setattr("app.config.settings.SMTP_HOST", "")
    cfg = email_svc.resolve_smtp_config(db)
    assert cfg.is_configured is False


def test_invalid_tls_mode_falls_back(db, make_user, monkeypatch):
    """A garbage tls_mode in the DB falls back to the port-based default."""
    monkeypatch.setattr("app.config.settings.SMTP_PORT", 587)
    admin = make_user(role="admin")
    settings_svc.set_value(
        db, key=settings_svc.Keys.SMTP_TLS_MODE, value="weird", actor=admin
    )
    db.commit()
    cfg = email_svc.resolve_smtp_config(db)
    assert cfg.tls_mode == "starttls"
