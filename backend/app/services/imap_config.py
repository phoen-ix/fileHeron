"""IMAP config resolution (v1.27.0) - DB-overlay-env, mirrors
``services/email.py::resolve_smtp_config``.

The behaviour knobs (enabled / check_mode / post-fetch action / notify mode) are
read here too so the worker and admin layers share one source of truth. Read live
per call - an admin change applies without a redeploy.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..config import settings
from . import settings as settings_svc

logger = logging.getLogger("fileheron.imap_config")

POST_FETCH_ACTIONS = ("mark_read", "untouched", "move", "delete")
NOTIFY_MODES = ("off", "human", "all")
TLS_MODES = ("implicit", "starttls", "none")


@dataclass(frozen=True)
class ImapConfig:
    """Resolved IMAP connection settings. Build via ``resolve_imap_config(db)`` -
    never read ``app.config.settings.IMAP_*`` directly outside that resolver."""
    host: str
    port: int
    user: str
    password: str
    tls_mode: str  # 'implicit' (993) | 'starttls' (143) | 'none'
    mailbox: str
    # When true, the TLS handshake does not verify the server certificate or
    # hostname. This used to be the ONLY behaviour - see open_session.
    tls_insecure: bool = False

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

    # The ENV value is the fallback, like every other field here. It used to
    # pass `""`, so `IMAP_TLS_MODE` - declared in config.py, documented in
    # .env.example and in the README - was read by nothing and the mode was
    # always inferred from the port (audit #2). An operator who set
    # `IMAP_TLS_MODE=starttls` on port 993 got implicit TLS and no indication
    # that their setting had been ignored.
    tls_mode = _eff(settings_svc.Keys.IMAP_TLS_MODE, settings.IMAP_TLS_MODE) or ""
    if tls_mode not in TLS_MODES:
        tls_mode = "starttls" if port == 143 else "implicit"

    host = _eff(settings_svc.Keys.IMAP_HOST, settings.IMAP_HOST)
    user = _eff(settings_svc.Keys.IMAP_USER, settings.IMAP_USER)
    password = _eff(settings_svc.Keys.IMAP_PASSWORD, settings.IMAP_PASSWORD)

    # Reuse the outgoing-email (SMTP) login by default, so the admin doesn't
    # re-enter it. Username/password come from SMTP; host falls back to the SMTP
    # host only when no IMAP host is set (it often differs, e.g. imap. vs smtp.).
    # Port/TLS/mailbox stay IMAP-specific (SMTP 587/starttls is wrong for IMAP).
    if uses_smtp_credentials(db):
        from .email import resolve_smtp_config

        smtp = resolve_smtp_config(db)
        user = smtp.user
        password = smtp.password
        host = host or smtp.host

    # tls_mode 'none' sends IMAP credentials + message bodies in CLEARTEXT. It's
    # a deliberate opt-in (e.g. a localhost relay), but in production against a
    # remote host it is almost certainly a mistake - surface it loudly (audit L19).
    if (
        tls_mode == "none"
        and settings.is_production
        and host
        and host.lower() not in ("localhost", "127.0.0.1", "::1")
    ):
        logger.error(
            "IMAP tls_mode=none in production against remote host %r - credentials "
            "and message bodies are transmitted in CLEARTEXT; use implicit/starttls.",
            host,
        )

    return ImapConfig(
        host=host,
        port=port,
        user=user,
        password=password,
        tls_mode=tls_mode,
        mailbox=_eff(settings_svc.Keys.IMAP_MAILBOX, settings.IMAP_MAILBOX),
        tls_insecure=settings_svc.get_bool(
            db, settings_svc.Keys.IMAP_TLS_INSECURE, default=False
        ),
    )


def uses_smtp_credentials(db: Session) -> bool:
    return settings_svc.get_bool(
        db, settings_svc.Keys.IMAP_USE_SMTP_CREDENTIALS, default=True
    )


def is_enabled(db: Session) -> bool:
    return settings_svc.get_bool(db, settings_svc.Keys.IMAP_ENABLED, default=False)


def post_fetch_action(db: Session) -> str:
    v = settings_svc.get(db, settings_svc.Keys.IMAP_POST_FETCH_ACTION)
    return v if v in POST_FETCH_ACTIONS else "mark_read"


def move_folder(db: Session) -> str:
    return settings_svc.get(db, settings_svc.Keys.IMAP_MOVE_FOLDER) or "fileHeron/Processed"


def require_known_sender(db: Session) -> bool:
    """Whether ingest refuses mail from an address with no user account.

    CLAUDE.md and the product's model both say "no anonymous senders", and
    nothing implemented it: any internet sender could land admin-downloadable
    attachments on the storage backend, attributable to no user, counted
    against no quota and behind no rate limit - 50,000 x 40 MB fills the volume
    that MariaDB and every upload share (audit #2). Default ON, and admin
    -tunable for an instance that genuinely wants an open mailbox.
    """
    return settings_svc.get_bool(
        db, settings_svc.Keys.IMAP_REQUIRE_KNOWN_SENDER, default=True
    )


def notify_mode(db: Session) -> str:
    v = settings_svc.get(db, settings_svc.Keys.IMAP_NOTIFY_MODE)
    return v if v in NOTIFY_MODES else "off"
