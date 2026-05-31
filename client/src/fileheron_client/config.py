"""On-disk config + keyring-backed secret storage.

Config (server URL, last email, etc.) lives in a JSON file under
the platform-appropriate user-config dir (``%APPDATA%\\fileHeron``
on Windows, ``~/.config/fileheron`` elsewhere). Secrets (the refresh
token or an API token) live in the OS keyring so they survive app
restarts but never end up in a flat file on disk.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import keyring
import platformdirs

APP_NAME = "fileHeron"
KEYRING_SERVICE = "fileheron-client"


def _config_dir() -> Path:
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))


def config_path() -> Path:
    return _config_dir() / "config.json"


@dataclass
class ClientConfig:
    server_url: str = ""
    last_email: Optional[str] = None
    auth_kind: str = "password"  # 'password' | 'api_token'
    last_landing: str = "inbox"  # one of 'inbox' | 'outbox' | 'new'
    # v0.4.16: gate verbose diagnostic logging (trace.log breadcrumbs,
    # app.log, heartbeat polling). crash.log + faulthandler always on.
    enable_diagnostic_logging: bool = False
    # v0.8.0: cached locale code from the last sign-in so the pre-login
    # screen renders in the user's language without a round trip.
    # Empty = use server's users.locale at sign-in; valid: 'en', 'de'.
    locale: str = ""

    def normalised_server_url(self) -> str:
        return (self.server_url or "").rstrip("/")


def load_config() -> ClientConfig:
    p = config_path()
    if not p.is_file():
        return ClientConfig()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ClientConfig()
    cfg = ClientConfig()
    for k in (
        "server_url",
        "last_email",
        "auth_kind",
        "last_landing",
        "enable_diagnostic_logging",
        "locale",
    ):
        if k in raw and raw[k] is not None:
            setattr(cfg, k, raw[k])
    return cfg


def save_config(cfg: ClientConfig) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    # Restrict to owner-only (finding L9). Best-effort: chmod is a no-op on
    # Windows ACLs but harmless; on POSIX it stops other local users reading
    # the server URL / last email from a shared machine.
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def normalize_server_url(raw: str) -> str:
    """Validate + normalise a user-entered server URL (finding L9).

    Enforces https so credentials can't be sent in cleartext to a
    socially-engineered http:// endpoint. Plain http is allowed ONLY for
    localhost / 127.0.0.1 (local dev). Raises ValueError on anything else.
    """
    from urllib.parse import urlparse

    s = (raw or "").strip().rstrip("/")
    if not s:
        raise ValueError("Server URL is required.")
    if "://" not in s:
        # Default to https when the user omits the scheme.
        s = "https://" + s
    parsed = urlparse(s)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https":
        return s
    if parsed.scheme == "http" and host in ("localhost", "127.0.0.1", "::1"):
        return s
    raise ValueError("Server URL must use https:// (http is only allowed for localhost).")


def _secret_username(kind: str, server_url: str) -> str:
    """Disambiguate secrets per server URL so a user can have configs
    for multiple servers without them stomping on each other."""
    return f"{kind}:{server_url.rstrip('/')}"


def get_secret(kind: str, server_url: str) -> Optional[str]:
    """Read a secret from the keyring. Returns None if missing."""
    return keyring.get_password(
        KEYRING_SERVICE, _secret_username(kind, server_url)
    )


def set_secret(kind: str, server_url: str, value: str) -> None:
    keyring.set_password(
        KEYRING_SERVICE, _secret_username(kind, server_url), value
    )


def clear_secret(kind: str, server_url: str) -> None:
    try:
        keyring.delete_password(
            KEYRING_SERVICE, _secret_username(kind, server_url)
        )
    except keyring.errors.PasswordDeleteError:
        pass
