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



def _header_safe(value: str) -> str:
    """Strip control characters from a value destined for a mail header."""
    collapsed = "".join(" " if (ch < " " or ch == "\x7f") else ch for ch in value)
    # The ASCII sweep above misses U+0085, U+2028 and U+2029, which `str.splitlines`
    # still counts as line breaks - and `str.splitlines` is the exact test
    # email.policy.default applies before it rejects a header. Joining its output is
    # what guarantees the result can never be refused.
    return " ".join(collapsed.splitlines()).strip()


# RFC 8058 s3.1 fixes this value and clients match it LITERALLY. It read
# `List=One-Click` for as long as the header existed, which no client honours,
# so one-click silently degraded to the mailto fallback (or to nothing) for
# every opt-outable category. A constant, so a test can assert on the value the
# code actually emits rather than on a copy of it.
ONE_CLICK_POST_VALUE = "List-Unsubscribe=One-Click"


def build_message(
    cfg,
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    list_unsubscribe: str | None = None,
) -> EmailMessage:
    """Assemble the outgoing message. Split out of `send_email` so the headers
    can be asserted on the built object instead of on the source text - the
    send path itself needs SMTP and returns before building anything when SMTP
    is unconfigured, so there was no seam to test through."""
    msg = EmailMessage()
    msg["From"] = cfg.from_header
    msg["To"] = to
    # Defence in depth on top of the display-name validation. EmailMessage
    # raises ValueError on CR/LF in a header value, so an unsanitised value does
    # not inject a header - it kills the send outright, and the sender of a
    # notification is not the person who suffers. Collapse control characters
    # here so no caller, present or future, can wedge outbound mail with one
    # (audit 2026-07-30).
    msg["Subject"] = _header_safe(subject)
    if list_unsubscribe:
        msg["List-Unsubscribe"] = list_unsubscribe
        # Signals RFC 8058 one-click support to Gmail/Outlook.
        msg["List-Unsubscribe-Post"] = ONE_CLICK_POST_VALUE
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    return msg


async def send_email(
    *,
    cfg: SmtpConfig,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    list_unsubscribe: str | None = None,
) -> None:
    """Send (or log) one email. Raises aiosmtplib exceptions on
    failure when SMTP is configured; logs-fallback on the dev path
    never raises.

    ``list_unsubscribe``: when set, adds the RFC 8058 one-click
    unsubscribe headers (``List-Unsubscribe`` + ``List-Unsubscribe-Post``)."""
    if not cfg.is_configured:
        from ..config import settings

        if settings.is_production:
            # An unconfigured SMTP host in production is a misconfiguration, not a
            # dev convenience. Printing the body would dump LIVE one-time tokens
            # (password-reset / verify / invite links) into the container logs,
            # readable by anyone with log access - defeating the mail-log's
            # fail-closed masking (audit M13). Log metadata only; the admin
            # mail-log keeps a masked copy of the body.
            logger.error(
                "EMAIL NOT SENT: SMTP unconfigured in production - body suppressed",
                extra={"to": to, "subject": subject},
            )
            return
        # Dev logs-fallback: print the email body block to stdout for visibility.
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

    msg = build_message(
        cfg,
        to=to,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        list_unsubscribe=list_unsubscribe,
    )

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
