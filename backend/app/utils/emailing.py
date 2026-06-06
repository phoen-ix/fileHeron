"""Email sending with logs-fallback when SMTP is unconfigured.

Pure-functional: takes an `SmtpConfig` and never touches `app.config`
directly. Callers (services/email.py, the ARQ worker, the admin
test-send endpoint) build the config from a DB-overlay-env resolver
and pass it in. This keeps the util layer DB-free and trivially
testable.

In dev (SMTP_HOST empty) we log the rendered email to stdout so
verification links etc. are visible without a real mailserver. In
production with SMTP configured we send via aiosmtplib.

Phase 6a wraps this in an ARQ worker with retry + backoff.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from email.message import EmailMessage

import aiosmtplib

logger = logging.getLogger("fileheron.email")


@dataclass(frozen=True)
class SmtpConfig:
    """Resolved SMTP settings used by the send pipeline. Build via
    `services/email.py::resolve_smtp_config(db)` - never read
    `app.config.settings.SMTP_*` directly outside that resolver."""
    host: str
    port: int
    user: str
    password: str
    from_email: str
    from_name: str
    # 'implicit' (TLS from connect, port 465 convention),
    # 'starttls'  (upgrade after greeting, port 587 convention),
    # 'none'      (plain SMTP - only sane on localhost / private MTA)
    tls_mode: str = "starttls"
    # EHLO/HELO name. Empty → aiosmtplib falls back to socket.getfqdn().
    helo_hostname: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.host)

    @property
    def from_header(self) -> str:
        return f"{self.from_name} <{self.from_email}>"


async def send_email(
    *,
    cfg: SmtpConfig,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> None:
    """Send (or log) one email. Raises aiosmtplib exceptions on
    failure when SMTP is configured; logs-fallback on the dev path
    never raises."""
    if not cfg.is_configured:
        # Logs-fallback: print the email body block to stdout for dev visibility.
        logger.info(
            "EMAIL DEV (no SMTP_HOST configured) - would send",
            extra={
                "to": to,
                "subject": subject,
                "body_preview": text_body[:500],
            },
        )
        # Also print a clearly-marked block to make verify links easy to spot.
        print("\n" + "=" * 72)
        print(f"EMAIL DEV → {to}")
        print(f"Subject:   {subject}")
        print("-" * 72)
        print(text_body)
        print("=" * 72 + "\n", flush=True)
        return

    msg = EmailMessage()
    msg["From"] = cfg.from_header
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    use_tls = cfg.tls_mode == "implicit"
    start_tls = cfg.tls_mode == "starttls"

    await aiosmtplib.send(
        msg,
        hostname=cfg.host,
        port=cfg.port,
        username=cfg.user or None,
        password=cfg.password or None,
        use_tls=use_tls,
        start_tls=start_tls,
        local_hostname=cfg.helo_hostname or None,
        timeout=20,
    )
