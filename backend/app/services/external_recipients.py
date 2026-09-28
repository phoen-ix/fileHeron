"""Share recipients with no account: the share's public link, sent by email.

Off by default (`share.external_recipients.enabled`). When on, a sender who may
create public links can address a share to a bare email address. That person
never becomes a principal - no user row, no connection, no in-app notice - and
the share's public link, with its own password and download counter, is their
only way in. The feature saves the sender from pasting the link into a mail of
their own, and gives nothing a pasted link would not: that is why the public
link policy is the gate, and why no account lookup is made on the address (an
answer of "this address has an account" would tell an employee that a client
they are not connected to exists).

The mail goes out from `share._dispatch_share_created`, which every announcement
path funnels through, so it is never sent before the files land or while the
share awaits approval - the link would not work yet.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.file import FileState
from ..models.public_link import PublicLink
from ..models.share import Share, ShareKind
from ..models.share_external_recipient import ShareExternalRecipient
from ..models.user import User, UserRole
from ..utils.timeutil import utc_now
from . import settings as settings_svc

logger = logging.getLogger("fileheron.external_recipients")

TEMPLATE_SLUG = "share_link_external"
# The mail-log category, and the key `mail_log._AUTH_LINK_CATEGORIES` lists so
# the log never offers to resend a live link.
MAIL_CATEGORY = TEMPLATE_SLUG
MAX_PER_SHARE = 20


def is_enabled(db: Session) -> bool:
    return settings_svc.get_bool(
        db, settings_svc.Keys.SHARE_EXTERNAL_RECIPIENTS_ENABLED, default=False
    )


def offers_invite(db: Session) -> bool:
    """The stored "ask the sender whether to also invite them" switch, as the
    admin set it. Whether a given sender is asked is `may_offer_invite`."""
    return settings_svc.get_bool(
        db, settings_svc.Keys.SHARE_EXTERNAL_RECIPIENTS_OFFER_INVITE, default=False
    )


def may_send(db: Session, user: User) -> bool:
    """True if `user` may address a share to an address with no account."""
    if user.role == UserRole.client or not is_enabled(db):
        return False
    from . import public_link as public_link_svc

    return public_link_svc.is_allowed_to_create(db, user)


def may_offer_invite(db: Session, user: User) -> bool:
    """True if the compose form should ask `user` whether to also invite the
    address. Staff only - the invite route itself admits admins and employees,
    and an employee may invite clients, which is what this offers."""
    return may_send(db, user) and offers_invite(db)


def assert_may_send(
    db: Session, user: User, *, kind: ShareKind, emails: list[str], has_link: bool
) -> None:
    """Refuse a create request carrying external addresses the sender may not
    use. Runs before anything is written."""
    if not emails:
        return
    if kind != ShareKind.outbound or user.role == UserRole.client:
        raise AppError(
            403,
            "FORBIDDEN_KIND",
            "Only outbound shares can be sent to an email address.",
        )
    if not is_enabled(db):
        raise AppError(
            403,
            "EXTERNAL_RECIPIENTS_DISABLED",
            "Sending to an address without an account is turned off on this instance.",
        )
    if not has_link:
        raise AppError(
            400,
            "EXTERNAL_RECIPIENT_NEEDS_LINK",
            "A recipient without an account can only be reached through a public link.",
        )


def add_to_share(db: Session, share: Share, emails: list[str]) -> list[ShareExternalRecipient]:
    """Write one row per distinct address. Caller flushes (and must, before
    `share_approval.is_approval_required` reads them)."""
    rows = [
        ShareExternalRecipient(share_id=share.id, email=email)
        for email in dict.fromkeys(emails)
    ]
    db.add_all(rows)
    return rows


def addresses(db: Session, share_id: str) -> list[str]:
    return [
        email
        for (email,) in db.query(ShareExternalRecipient.email)
        .filter(ShareExternalRecipient.share_id == share_id)
        .order_by(ShareExternalRecipient.id)
        .all()
    ]


def has_any(db: Session, share_id: str) -> bool:
    return (
        db.query(ShareExternalRecipient.id)
        .filter(ShareExternalRecipient.share_id == share_id)
        .first()
        is not None
    )


def send_links(db: Session, share: Share) -> int:
    """Mail the share's public link to every address not yet told. Returns how
    many were queued. Caller commits; the sends go out after the commit.

    Called from the share announcement, so the share is active and its files
    have landed. Nothing is sent - and the rows stay unstamped - when the link
    is gone or its URL cannot be rebuilt: a mail without a working link is
    worse than none, and the sender can see who was never told.
    """
    pending = (
        db.query(ShareExternalRecipient)
        .filter(
            ShareExternalRecipient.share_id == share.id,
            ShareExternalRecipient.notified_at.is_(None),
        )
        .order_by(ShareExternalRecipient.id)
        .all()
    )
    if not pending:
        return 0
    from . import public_link as public_link_svc

    link = (
        db.query(PublicLink)
        .filter(PublicLink.share_id == share.id, PublicLink.revoked_at.is_(None))
        .one_or_none()
    )
    url = public_link_svc.stored_url(db, link) if link is not None else None
    if link is None or url is None:
        logger.warning(
            "share %s: %d external recipient(s) not mailed - no usable public link",
            share.id,
            len(pending),
        )
        return 0

    from . import email as email_svc
    from . import mail_log
    from . import notification as notif_svc
    from . import site as site_svc

    sender = share.created_by or db.get(User, share.created_by_id)
    payload = {
        "sender_name": sender.display_name if sender else "",
        "subject": share.subject,
        "message": share.message,
        "expires_at": share.expires_at,
        "file_count": sum(1 for f in share.files if f.state != FileState.deleted),
        "link_url": url,
        "has_password": link.password_hash is not None,
    }
    # Rendered ONCE for every address, in the sender's locale (nothing is known
    # about the recipient), and with no recipient passed: an address with no
    # account has no preferences to manage, so no unsubscribe footer - the
    # same treatment as the custom alert addresses in `error_alert`.
    subject, text, html = email_svc.render_email(
        sender.locale if sender else "en",
        TEMPLATE_SLUG,
        payload,
        app_url=site_svc.get_site_url(db),
        site_timezone=site_svc.get_site_timezone(db),
        app_name=site_svc.get_app_name(db),
        db=db,
    )
    now = utc_now()
    for row in pending:
        eid = mail_log.record_queued(
            db,
            recipient_email=row.email,
            recipient_user_id=None,
            category=MAIL_CATEGORY,
            template_slug=TEMPLATE_SLUG,
            subject=subject,
            text_body=text,
            html_body=html,
        )
        row.notified_at = now
        notif_svc._queue_email_job(
            db,
            {
                "to": row.email,
                "subject": subject,
                "text_body": text,
                "html_body": html,
                "email_log_id": eid,
            },
        )
    db.flush()
    return len(pending)
