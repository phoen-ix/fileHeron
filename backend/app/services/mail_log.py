"""Mail log - record every outbound email (v1.11.0).

Single funnel for the `email_log` table. Every send path calls one of:

- ``record_queued()`` - the notification dispatcher creates a queued row and
  threads its id into the ARQ job; the worker ``finalize()``s it.
- ``finalize()`` - the worker UPDATEs the queued row to its terminal status
  (sent/failed/error). **One row per email** - retries bump ``attempts``.
- ``record_direct()`` - single-shot create+finalize for the synchronous direct
  senders, the admin test-send, and the dev logs-fallback.

Bodies are stored with one-time auth-link tokens **masked at rest** so the log
can never be used to take over an account and a short-lived token doesn't
outlive its TTL in a browsable log. Masking fails **closed**. A log-write
failure must never break the actual send - every public helper swallows and
logs its own errors.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from ..models.email_log import EmailLog, EmailStatus, EmailVia

logger = logging.getLogger("fileheron.mail_log")

# Token segment of the one-time auth links built in services/email.py:
#   {site}/reset-password/{token}, /verify-email/{token}, /register/{token},
#   /confirm-email-change/{token}, /cancel-email-change/{token}
_AUTH_LINK_RE = re.compile(
    r"(/(?:reset-password|verify-email|register|confirm-email-change"
    r"|cancel-email-change)/)([A-Za-z0-9._~\-]+)"
)
_REDACTED = r"\1<redacted>"

# The long-lived manage-notifications / unsubscribe footer token added to every
# notification email. Lower sensitivity than the auth links above (it only
# governs notification prefs), but it must still not sit in plaintext in the
# browsable mail log (audit L9/L28). Masked separately so it does NOT disable
# resend the way an account-takeover token does.
_FOOTER_LINK_RE = re.compile(r"(/manage-notifications/)([A-Za-z0-9._~\-]+)")

# Categories whose emails always carry a one-time token - RESEND is hard-disabled
# on these even if a future template tweak moves the token out of regex reach.
_AUTH_LINK_CATEGORIES = {
    "reset_password",
    "password_reset",
    "verify",
    "invite",
    "lockout_warning",
    "email_change_confirm",
    "email_change_verify_old",
    "email_change_alert",
}

_BODY_UNAVAILABLE = "[body unavailable: masking error]"


def mask_sensitive(text: str | None) -> tuple[str | None, bool]:
    """Redact one-time auth-link tokens. Returns ``(text, did_redact)``.

    Fails **closed**: on any error, drop the body to a placeholder and mark it
    redacted - never return a body that might still contain a live token."""
    if text is None:
        return None, False
    try:
        new, n = _AUTH_LINK_RE.subn(_REDACTED, text)
        return new, n > 0
    except Exception:
        logger.exception("mail_log: masking failed; dropping body")
        return _BODY_UNAVAILABLE, True


def mask_bodies(
    text_body: str | None, html_body: str | None, category: str | None
) -> tuple[str | None, str | None, bool]:
    """Mask both bodies. ``masked`` is true when either was redacted OR the
    category is a known token-bearing one (so resend stays disabled regardless)."""
    t, t_red = mask_sensitive(text_body)
    h, h_red = mask_sensitive(html_body)
    # Redact the manage-notifications footer token too (low sensitivity -> does
    # not flip `masked`/disable resend; audit L9/L28).
    t = _mask_footer(t)
    h = _mask_footer(h)
    masked = t_red or h_red or (category in _AUTH_LINK_CATEGORIES)
    return t, h, masked


def _mask_footer(text: str | None) -> str | None:
    if not text:
        return text
    try:
        return _FOOTER_LINK_RE.sub(_REDACTED, text)
    except Exception:
        return text


def record_queued(
    db: Session,
    *,
    recipient_email: str,
    recipient_user_id: int | None,
    category: str | None,
    template_slug: str | None,
    subject: str,
    text_body: str | None,
    html_body: str | None,
) -> int | None:
    """Insert a ``queued`` row for the notification path. Returns its id
    (threaded into the ARQ job so the worker can finalize it), or None on
    failure (the email still sends)."""
    try:
        t, h, masked = mask_bodies(text_body, html_body, category)
        row = EmailLog(
            recipient_email=recipient_email,
            recipient_user_id=recipient_user_id,
            category=category,
            template_slug=template_slug,
            via=EmailVia.queued,
            status=EmailStatus.queued,
            subject=subject[:512],
            body_text=t,
            body_html=h,
            masked=masked,
            attempts=0,
        )
        db.add(row)
        db.flush()
        return row.id
    except Exception:
        logger.exception("mail_log.record_queued failed for %s", recipient_email)
        return None


def finalize(
    db: Session,
    *,
    email_log_id: int,
    status: EmailStatus,
    attempt: int,
    via: EmailVia | None = None,
    smtp_code: int | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
) -> None:
    """UPDATE the queued row to its in-flight / terminal status. A missing row
    is logged and skipped (handles the enqueue-then-rollback orphan). One row
    per email - ``attempts`` only ever climbs."""
    try:
        row = db.get(EmailLog, email_log_id)
        if row is None:
            logger.info("mail_log.finalize: row %s gone; skipping", email_log_id)
            return
        row.status = status
        row.attempts = max(row.attempts or 0, attempt)
        if via is not None:
            row.via = via
        if smtp_code is not None:
            row.smtp_code = smtp_code
        if error_class is not None:
            row.error_class = error_class[:64]
        if error_message is not None:
            row.error_message = error_message[:500]
        db.flush()
    except Exception:
        logger.exception("mail_log.finalize failed for row %s", email_log_id)


def record_direct(
    db: Session,
    *,
    recipient_email: str,
    recipient_user_id: int | None,
    category: str | None,
    template_slug: str | None,
    via: EmailVia,
    subject: str,
    text_body: str | None,
    html_body: str | None,
    status: EmailStatus,
    smtp_code: int | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
) -> int | None:
    """Single-shot create+finalize for the non-queued paths (direct senders,
    admin test-send, dev logs-fallback)."""
    try:
        t, h, masked = mask_bodies(text_body, html_body, category)
        row = EmailLog(
            recipient_email=recipient_email,
            recipient_user_id=recipient_user_id,
            category=category,
            template_slug=template_slug,
            via=via,
            status=status,
            subject=subject[:512],
            body_text=t,
            body_html=h,
            masked=masked,
            attempts=0 if via == EmailVia.dev_fallback else 1,
            smtp_code=smtp_code,
            error_class=error_class[:64] if error_class else None,
            error_message=error_message[:500] if error_message else None,
        )
        db.add(row)
        db.flush()
        return row.id
    except Exception:
        logger.exception("mail_log.record_direct failed for %s", recipient_email)
        return None
