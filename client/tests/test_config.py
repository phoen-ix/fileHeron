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
