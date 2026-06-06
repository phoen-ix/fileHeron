"""IMAP config resolution (v1.27.0) — DB-overlay-env, mirrors
``services/email.py::resolve_smtp_config``.

The behaviour knobs (enabled / check_mode / post-fetch action / notify mode) are
read here too so the worker and admin layers share one source of truth. Read live
per call — an admin change applies without a redeploy.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..config import settings
from . import settings as settings_svc

POST_FETCH_ACTIONS = ("mark_read", "untouched", "move", "delete")
NOTIFY_MODES = ("off", "human", "all")
TLS_MODES = ("implicit", "starttls", "none")


@dataclass(frozen=True)
class ImapConfig:
    """Resolved IMAP connection settings. Build via ``resolve_imap_config(db)`` —
    never read ``app.config.settings.IMAP_*`` directly outside that resolver."""
    host: str
    port: int
    user: str
    password: str
    tls_mode: str  # 'implicit' (993) | 'starttls' (143) | 'none'
    mailbox: str

    @property
    def is_configured(self) -> bool:
        return bool(self.host)


def resolve_imap_config(db: Session) -> ImapConfig:
    """DB-overlay-env for every IMAP connection field."""
    def _eff(key: str, env_val: str) -> str:
        v = settings_svc.get(db, key)
        return v if v is not None else env_val

    port_str = _eff(settings_svc.Keys.IMAP_PORT, str(settings.IMAP_PORT))
    try:
        port = int(port_str)
    except (TypeError, ValueError):
        port = settings.IMAP_PORT

    tls_mode = _eff(settings_svc.Keys.IMAP_TLS_MODE, "") or ""
    if tls_mode not in TLS_MODES:
        tls_mode = "starttls" if port == 143 else "implicit"

    return ImapConfig(
        host=_eff(settings_svc.Keys.IMAP_HOST, settings.IMAP_HOST),
        port=port,
        user=_eff(settings_svc.Keys.IMAP_USER, settings.IMAP_USER),
        password=_eff(settings_svc.Keys.IMAP_PASSWORD, settings.IMAP_PASSWORD),
        tls_mode=tls_mode,
        mailbox=_eff(settings_svc.Keys.IMAP_MAILBOX, settings.IMAP_MAILBOX),
    )


def is_enabled(db: Session) -> bool:
    return settings_svc.get_bool(db, settings_svc.Keys.IMAP_ENABLED, default=False)


def check_mode(db: Session) -> str:
    return settings_svc.get(db, settings_svc.Keys.IMAP_CHECK_MODE) or "auto"


def post_fetch_action(db: Session) -> str:
    v = settings_svc.get(db, settings_svc.Keys.IMAP_POST_FETCH_ACTION)
    return v if v in POST_FETCH_ACTIONS else "mark_read"


def move_folder(db: Session) -> str:
    return settings_svc.get(db, settings_svc.Keys.IMAP_MOVE_FOLDER) or "fileHeron/Processed"


def notify_mode(db: Session) -> str:
    v = settings_svc.get(db, settings_svc.Keys.IMAP_NOTIFY_MODE)
    return v if v in NOTIFY_MODES else "off"
