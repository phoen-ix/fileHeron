"""Round-trip ClientConfig + keyring access."""
from __future__ import annotations

from fileheron_client import config


def test_load_config_returns_defaults_when_missing(tmp_config_dir):
    cfg = config.load_config()
    assert cfg.server_url == ""
    assert cfg.last_email is None
    assert cfg.auth_kind == "password"


def test_save_then_load_roundtrip(tmp_config_dir):
    cfg = config.ClientConfig(
        server_url="https://files.example.com/",
        last_email="alice@example.com",
        auth_kind="api_token",
        last_landing="outbox",
    )
    config.save_config(cfg)
    loaded = config.load_config()
    assert loaded.server_url == "https://files.example.com/"
    assert loaded.last_email == "alice@example.com"
    assert loaded.auth_kind == "api_token"
    assert loaded.last_landing == "outbox"


def test_normalised_server_url_strips_trailing_slash():
    assert (
        config.ClientConfig(server_url="https://x.example.com/").normalised_server_url()
        == "https://x.example.com"
    )


def test_secret_roundtrip_per_server(tmp_keyring):
    config.set_secret("refresh", "https://a.example.com", "tok-a")
    config.set_secret("refresh", "https://b.example.com", "tok-b")
    assert config.get_secret("refresh", "https://a.example.com") == "tok-a"
    assert config.get_secret("refresh", "https://b.example.com") == "tok-b"
    config.clear_secret("refresh", "https://a.example.com")
    assert config.get_secret("refresh", "https://a.example.com") is None
    assert config.get_secret("refresh", "https://b.example.com") == "tok-b"


def test_clear_secret_for_unknown_is_noop(tmp_keyring):
    # Should not raise even if nothing's there.
    config.clear_secret("refresh", "https://nothing.example.com")


# ---- normalize_server_url (finding L9: HTTPS enforcement) ------------------


def test_normalize_server_url_defaults_to_https():
    assert config.normalize_server_url("files.example.com") == "https://files.example.com"
    assert config.normalize_server_url("https://files.example.com/") == "https://files.example.com"


def test_normalize_server_url_rejects_plain_http_remote():
    import pytest

    with pytest.raises(ValueError):
        config.normalize_server_url("http://files.example.com")
    with pytest.raises(ValueError):
        config.normalize_server_url("")


def test_normalize_server_url_allows_http_localhost():
    assert config.normalize_server_url("http://localhost:8000") == "http://localhost:8000"
    assert config.normalize_server_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"


# ---- C1: keyring failures must degrade, not crash --------------------------


def test_set_secret_swallows_keyring_failure(monkeypatch):
    import keyring as _kr

    def _boom(*_a, **_kw):
        raise _kr.errors.KeyringError("backend unavailable")

    monkeypatch.setattr(_kr, "set_password", _boom)
    # Must NOT raise — sign-in continues; token stays in memory.
    config.set_secret("api_token", "https://srv.example", "tok123")


def test_get_secret_returns_none_on_keyring_failure(monkeypatch):
    import keyring as _kr

    def _boom(*_a, **_kw):
        raise _kr.errors.KeyringError("locked")

    monkeypatch.setattr(_kr, "get_password", _boom)
    assert config.get_secret("api_token", "https://srv.example") is None


# ---- C7: save_config is atomic ---------------------------------------------


def test_save_config_is_atomic_no_partial_file(tmp_config_dir, monkeypatch):
    good = config.ClientConfig(server_url="https://files.example.com", last_email="a@b.c")
    config.save_config(good)
    # Simulate a crash mid-write: make the temp write blow up AFTER an
    # earlier good file exists. The old file must remain intact.
    real_write = config.Path.write_text

    def _boom(self, *_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(config.Path, "write_text", _boom)
    with __import__("pytest").raises(OSError):
        config.save_config(config.ClientConfig(server_url="https://evil", last_email="x"))
    # The previously-saved good config is still readable + unchanged.
    loaded = config.load_config()
    assert loaded.server_url == "https://files.example.com"
    assert loaded.last_email == "a@b.c"


# ---- C8: reject embedded credentials in the server URL ---------------------


def test_normalize_server_url_rejects_userinfo():
    import pytest
    with pytest.raises(ValueError):
        config.normalize_server_url("https://user:pass@files.example.com")
    with pytest.raises(ValueError):
        config.normalize_server_url("https://admin@files.example.com")
