"""GDPR right-to-erasure flow.

When admin requests erasure of user X:

1. **User row anonymized** - `email` rewritten to
   `erased-<id>@erased.invalid` so it never collides with a future
   signup; `display_name` set to `[erased]`; `password_hash` blanked
   (login impossible); `is_disabled` set true; `oidc_subject` cleared.
2. **Files uploaded by X hard-deleted** - every `files` row where
   `uploaded_by_id == X.id`. Disk unlink + `state=deleted` + audit.
3. **Recipient references kept** - `share_recipients.recipient_user_id`
   stays as the FK; the row's display value is now `[erased]`. The
   sender's data (the share itself, files in it) is preserved.
4. **TOTP / recovery codes / refresh tokens / API tokens / sessions
   wiped** - anything that could grant ongoing access.
5. **Audit event** - `user_erased` with the admin as actor.

The flow is irreversible. Admin UI confirms with a two-step modal that
shows the file count + total bytes about to disappear.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.api_token import ApiToken
from ..models.audit_log import AuditEventType, AuditLog
from ..models.client_employee_connection import ClientEmployeeConnection
from ..models.download_log import DownloadLog
from ..models.email_change_token import EmailChangeToken
from ..models.email_log import EmailLog
from ..models.email_verify_token import EmailVerifyToken
from ..models.file import File, FileState
from ..models.invite_token import InviteToken
from ..models.known_device import KnownDevice
from ..models.login_attempt import LoginAttempt
from ..models.notification import Notification
from ..models.password_reset_token import PasswordResetToken
from ..models.refresh_token import RefreshToken
from ..models.user import User
from ..models.user_recovery_code import UserRecoveryCode
from ..models.user_totp import UserTOTP
from ..models.user_webauthn_credential import UserWebAuthnCredential
from ..utils.timeutil import utc_now
from . import file as file_svc
from .audit import record_audit_event


def _is_erased(user: User) -> bool:
    return bool(user.email) and user.email.endswith("@erased.invalid")

logger = logging.getLogger("fileheron.erasure")




def _unlink_tus_partial(tus_upload_id: str) -> None:
    """Remove a tusd working file and its .info sidecar.

    Deliberately duplicates the shape of
    workers/cleanup_stale_uploads._unlink_partial_bytes rather than importing
    it: that helper takes a File and also deletes from the storage backend,
    which hard_delete has already done by the time we get here. Best-effort -
    a failure here must not abort an erasure that has already destroyed
    committed data."""
    from pathlib import Path

    from ..config import settings

    base = Path(settings.TUS_UPLOAD_DIR)
    for p in (base / tus_upload_id, base / f"{tus_upload_id}.info"):
        try:
            if p.is_file():
                p.unlink()
        except OSError as e:
            logger.warning("erasure: tus partial unlink failed %s: %s", p, e)


def erase_user(
    db: Session, *, actor: User, target: User, request=None
) -> dict:
    """Run the erasure. Returns a small summary dict (file count + bytes).

    Caller commits."""
    if target.id == actor.id:
        raise AppError(
            400, "CANNOT_ERASE_SELF", "An admin cannot erase their own account."
        )
    # Serialise on the target row BEFORE the already-erased check. `_is_erased`
    # was an unsynchronised read, so a double-submitted erase (an impatient
    # click, or two admins acting on the same GDPR request) had both requests
    # pass the check, both walk the file loop, and both release the same quota -
    # producing two conflicting `user_erased` receipts for one person. The
    # second request now blocks here and loses the race cleanly with 409
    # (audit 2026-07-30).
    q = db.query(User).filter(User.id == target.id)
    # SQLite has no row locks and rejects FOR UPDATE, so the guard applies on
    # the engine production runs (MariaDB) and degrades to a plain re-read in
    # the test harness. The ordering - lock/re-read BEFORE the erased check -
    # is what removes the race, and that is asserted separately.
    if db.bind is not None and db.bind.dialect.name != "sqlite":
        q = q.with_for_update()
    locked = q.one_or_none()
    if locked is None:
        raise AppError(404, "USER_NOT_FOUND", "User not found.")
    target = locked
    if _is_erased(target):
        raise AppError(409, "ALREADY_ERASED", "This user has already been erased.")

    # 1. Hard-delete files this user uploaded. If any unlink fails, abort
    # the whole erasure - partial erasure would leave the user
    # half-anonymised AND lie in the receipt PDF that admins hand back.
    # Better: raise, let admin clean the disk, retry.
    files = (
        db.query(File)
        .filter(
            File.uploaded_by_id == target.id,
            File.state != FileState.deleted,
        )
        .all()
    )
    deleted_count = 0
    deleted_bytes = 0
    failed_files: list[str] = []
    for f in files:
        f_bytes = f.size_bytes
        f_id = f.id
        tus_id = f.tus_upload_id
        try:
            file_svc.hard_delete(
                db, file=f, reason="user_erased", actor_user_id=actor.id, request=request
            )
            # An `uploading` row has no storage_path yet, so hard_delete unlinks
            # nothing while the receipt still credits its full declared size.
            # The bytes live in tusd's working directory until
            # cleanup_abandoned_uploads runs - hours later. Mirror
            # cleanup_stale_uploads._unlink_partial_bytes so "erased" is true at
            # the moment we say it (audit 2026-07-30).
            if tus_id:
                _unlink_tus_partial(tus_id)
            # Commit each deletion durably. hard_delete unlinks the bytes BEFORE
            # marking the row deleted, so if a LATER file's unlink fails (which
            # aborts the whole erasure), a transaction rollback must not revert -
            # and thereby resurrect the DB rows of - files whose bytes are already
            # gone. Per-file commit makes each deletion final and the retry resume
            # cleanly from the failed file.
            # The row is retained as a `deleted` marker, but it kept
            # `original_filename` - and the admin file browser drops the
            # state filter under `include_inactive=true`, so an admin could
            # still list every filename the erased user had ever uploaded
            # (audit 2026-07-30). The marker's job is accounting, not content.
            f.original_filename = "[erased]"
            f.mime_type = "application/octet-stream"
            db.commit()
            deleted_bytes += f_bytes
            deleted_count += 1
        except OSError as e:
            db.rollback()
            logger.error("erasure: hard_delete failed file=%s: %s", f_id, e)
            failed_files.append(f_id)
    if failed_files:
        raise AppError(
            500,
            "ERASURE_FILE_DELETE_FAILED",
            f"{len(failed_files)} file(s) failed to delete; aborting erasure.",
            details={"failed_file_ids": failed_files},
        )

    # 1b. Revoke the target's still-live shares + their public links. The files
    # above are now deleted, so leaving these active would keep shares (and
    # anonymous public links) alive over dead files - a soft revoke closes them.
    from ..models.public_link import PublicLink
    from ..models.share import Share, ShareState
    from . import public_link as public_link_svc
    from . import share as share_svc
    live_shares = (
        db.query(Share)
        .filter(
            Share.created_by_id == target.id,
            Share.state.in_([ShareState.active, ShareState.pending_approval]),
        )
        .all()
    )
    for s in live_shares:
        link = db.query(PublicLink).filter(PublicLink.share_id == s.id).one_or_none()
        if link is not None and link.revoked_at is None:
            public_link_svc.revoke(db, actor=actor, link=link)
        share_svc.revoke_share(db, user=actor, share=s, request=request)

    # 2. Wipe credentials + tokens.
    db.query(UserTOTP).filter(UserTOTP.user_id == target.id).delete(
        synchronize_session=False
    )
    db.query(UserRecoveryCode).filter(
        UserRecoveryCode.user_id == target.id
    ).delete(synchronize_session=False)
    db.query(RefreshToken).filter(RefreshToken.user_id == target.id).delete(
        synchronize_session=False
    )
    db.query(ApiToken).filter(ApiToken.owner_user_id == target.id).delete(
        synchronize_session=False
    )
    # WebAuthn credentials are device-bound personal data; anonymise-by-UPDATE
    # never CASCADEs them, so they'd otherwise survive the erasure (audit L13).
    db.query(UserWebAuthnCredential).filter(
        UserWebAuthnCredential.user_id == target.id
    ).delete(synchronize_session=False)
    # Pending email-change tokens carry the target's new/old PLAINTEXT email and
    # are never reaped while unsettled - delete them on erasure (audit L12).
    # Every address this person has ever held. Two sources, because neither is
    # complete on its own:
    #   - email_change_tokens.new_email covers addresses they asked to move TO,
    #     including a pending change that never settled. There is no old_email
    #     column; the token only records the destination.
    #   - the `email_changed` audit rows carry both sides of every completed
    #     change, and audit_log is deliberately retained, so it is the only
    #     record of an address they have already moved away from.
    prior_emails: set[str] = set()
    for (addr,) in db.query(EmailChangeToken.new_email).filter(
        EmailChangeToken.user_id == target.id
    ).all():
        if addr:
            prior_emails.add(addr)
    for (extra,) in db.query(AuditLog.extra).filter(
        AuditLog.event_type == AuditEventType.email_changed.value,
        AuditLog.target_id == str(target.id),
    ).all():
        if isinstance(extra, dict):
            prior_emails.update(
                e for e in (extra.get("old_email"), extra.get("new_email")) if e
            )
    db.query(EmailChangeToken).filter(
        EmailChangeToken.user_id == target.id
    ).delete(synchronize_session=False)

    # 3. Drop ClientEmployeeConnection rows pointing at this user - the
    # FK CASCADE doesn't fire because erasure anonymises rather than
    # deletes the row.
    db.query(ClientEmployeeConnection).filter(
        (ClientEmployeeConnection.client_user_id == target.id)
        | (ClientEmployeeConnection.employee_user_id == target.id)
    ).delete(synchronize_session=False)

    # 3a. And the group memberships, which are what DERIVE those connections.
    # Deleting the connections while leaving the memberships was self-undoing:
    # `connection.recompute_shared_group_connections_for_user` runs whenever
    # any co-member's groups change, reads `_users_sharing_a_group_with`, and
    # recreates a `shared_group` connection to the erased row. Membership is
    # also personal data in its own right - which groups a person belonged to
    # (audit 2026-07-30).
    from ..models.group_member import GroupMember

    pii_purged_group_members = (
        db.query(GroupMember)
        .filter(GroupMember.user_id == target.id)
        .delete(synchronize_session=False)
    )

    # 3b. Purge personal data that lives OUTSIDE the users row. Because
    # erasure anonymises (UPDATE) rather than DELETEs the user, no FK
    # CASCADE fires - these rows would otherwise retain plaintext email /
    # device fingerprints / IPs of a supposedly-erased user (GDPR Art. 17).
    # Captured BEFORE the email is rewritten below so the email-keyed
    # deletes still match.
    original_email = target.email
    # Every address this person has ever held, not just the current one.
    # login_attempts and invite_tokens are keyed by plaintext email, and the
    # email-change history was read (and deleted) a few lines above - so rows
    # written under a previous address survived an erasure that reported
    # itself complete. `_prior_emails` is captured before that delete
    # (audit 2026-07-30).
    all_emails = {original_email, *prior_emails}
    pii_purged: dict[str, int] = {}
    # Plaintext email in the forensic login-attempt log.
    pii_purged["login_attempts"] = (
        db.query(LoginAttempt)
        .filter(LoginAttempt.email.in_(all_emails))
        .delete(synchronize_session=False)
    )
    # Plaintext email in invites - both invites sent TO this user and
    # invites this user created (the latter also carry third-party emails).
    pii_purged["invite_tokens"] = (
        db.query(InviteToken)
        .filter(
            InviteToken.email.in_(all_emails)
            | (InviteToken.created_by_id == target.id)
        )
        .delete(synchronize_session=False)
    )
    # Device fingerprints (UA hash + IP geohash) are personal data.
    pii_purged["known_devices"] = (
        db.query(KnownDevice)
        .filter(KnownDevice.user_id == target.id)
        .delete(synchronize_session=False)
    )
    # This user's own bell notifications - payloads can embed names /
    # filenames. (Notifications to OTHER users that merely reference this
    # user by then-current name are their data, not ours to delete.)
    pii_purged["notifications"] = (
        db.query(Notification)
        .filter(Notification.user_id == target.id)
        .delete(synchronize_session=False)
    )
    # Dangling single-use auth tokens (no PII, but they're this user's).
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == target.id
    ).delete(synchronize_session=False)
    db.query(EmailVerifyToken).filter(
        EmailVerifyToken.user_id == target.id
    ).delete(synchronize_session=False)
    # Strip IP / UA from this user's download rows but KEEP the row (the
    # FK now points at the anonymised user) so the sender's "was it
    # downloaded" history survives without leaking the recipient's PII.
    pii_purged["download_log_scrubbed"] = (
        db.query(DownloadLog)
        .filter(DownloadLog.accessed_by_user_id == target.id)
        .update(
            {DownloadLog.ip: None, DownloadLog.ua_fingerprint_hash: None},
            synchronize_session=False,
        )
    )
    # Scrub the mail log: drop recipient PII (email + bodies/subject can embed
    # display_name / filenames) but KEEP the row so per-flow counts survive.
    # Match on user_id OR the plaintext email (invite/verify rows logged before
    # the invitee had an account carry recipient_user_id=NULL).
    pii_purged["email_log_scrubbed"] = (
        db.query(EmailLog)
        .filter(
            (EmailLog.recipient_user_id == target.id)
            | EmailLog.recipient_email.in_(all_emails)
        )
        .update(
            {
                EmailLog.recipient_user_id: None,
                EmailLog.recipient_email: f"erased-{target.id}@erased.invalid",
                EmailLog.body_text: None,
                EmailLog.body_html: None,
                EmailLog.subject: "[erased]",
                EmailLog.masked: True,
            },
            synchronize_session=False,
        )
    )
    # Inbound mailbox: a registered user who replied to a share by email leaves
    # their plaintext sender email + display name in inbound_messages, which
    # stays searchable in the admin inbox indefinitely (Art.17 residue). Scrub
    # the sender identity fields (mirror email_log) but keep the row + body as a
    # business record of received correspondence (audit M7).
    from ..models.inbound_attachment import InboundAttachment
    from ..models.inbound_message import InboundMessage

    # The v1.27 scrub anonymised the message SENDER but left the attachments:
    # their bytes on the storage backend, their filenames in the table, and the
    # admin download route pointing at both. A person who emailed a document in
    # and then exercised Art.17 had the document survive the erasure that
    # reported itself complete (audit 2026-07-30). Unlink the blobs and delete
    # the rows; the message row stays as a business record of correspondence
    # received, which is what the v1.27 decision was actually about.
    target_message_ids = [
        mid
        for (mid,) in db.query(InboundMessage.id)
        .filter(
            (InboundMessage.sender_user_id == target.id)
            | InboundMessage.sender_email.in_(all_emails)
        )
        .all()
    ]
    attachments_purged = 0
    if target_message_ids:
        from .storage_backend import get_storage_backend

        backend = get_storage_backend()
        rows = (
            db.query(InboundAttachment)
            .filter(InboundAttachment.message_id.in_(target_message_ids))
            .all()
        )
        for att in rows:
            try:
                backend.delete(att.storage_key)
            except Exception as e:
                # Best-effort, like every other unlink in this function past the
                # file loop: an orphaned blob is bad, an aborted erasure that
                # has already destroyed committed data is worse.
                logger.warning(
                    "erasure: inbound attachment unlink failed key=%s: %s",
                    att.storage_key, e,
                )
        attachments_purged = (
            db.query(InboundAttachment)
            .filter(InboundAttachment.message_id.in_(target_message_ids))
            .delete(synchronize_session=False)
        )
    pii_purged["inbound_attachments"] = attachments_purged

    pii_purged["inbound_messages_scrubbed"] = (
        db.query(InboundMessage)
        .filter(
            (InboundMessage.sender_user_id == target.id)
            | InboundMessage.sender_email.in_(all_emails)
        )
        .update(
            {
                InboundMessage.sender_user_id: None,
                InboundMessage.sender_email: f"erased-{target.id}@erased.invalid",
                InboundMessage.sender_name: None,
            },
            synchronize_session=False,
        )
    )

    # error_log keeps `ip` (the real client IP, resolved through the proxy) and
    # an FK-less `user_id`. It was absent from the purge, from pii_purged, and
    # from the "deliberately retained" note below - i.e. not a decision, an
    # omission. Scrub in place like download_log so scan-triage counts and the
    # 5xx history survive without the person in them (audit 2026-07-30).
    from ..models.error_log import ErrorLog

    pii_purged["error_log_scrubbed"] = (
        db.query(ErrorLog)
        .filter(ErrorLog.user_id == target.id)
        .update({ErrorLog.ip: None, ErrorLog.user_id: None}, synchronize_session=False)
    )
    pii_purged["group_members"] = pii_purged_group_members

    # audit_log rows are retained, but two event types carry the person's
    # plaintext addresses in their metadata, so the note below - "references the
    # user by anonymised id" - was never true of them. /admin/audit and its CSV
    # export handed back an erased subject's real addresses indefinitely, after
    # an erasure that issued a signed receipt saying otherwise. Keep the events,
    # drop the addresses. This runs AFTER `prior_emails` has been read off these
    # same rows, which is what makes the email-keyed purges above complete
    # (audit 2026-07-30).
    scrubbed = 0
    for row in (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type.in_(
                (
                    AuditEventType.email_changed.value,
                    AuditEventType.email_change_requested.value,
                )
            ),
            AuditLog.target_id == str(target.id),
        )
        .all()
    ):
        extra = dict(row.extra or {})
        present = [k for k in ("old_email", "new_email") if k in extra]
        if not present:
            continue
        for k in present:
            extra[k] = "[erased]"
        row.extra = extra
        scrubbed += 1
    pii_purged["audit_log_scrubbed"] = scrubbed

    # Deliberately retained: `share_recipients` rows reference the (now
    # anonymised) user by integer FK only - no plaintext PII - so the
    # sender's recipient list stays intact. `audit_log` is the append-only
    # legal record the erasure receipt verifies against; it references the
    # user by anonymised id.

    # 4. Anonymize the row. Email pattern keeps the UNIQUE(email)
    # constraint happy for repeat erasures and makes the row debuggable.
    target.email = f"erased-{target.id}@erased.invalid"
    target.display_name = "[erased]"
    target.password_hash = ""
    target.is_disabled = True
    target.oidc_subject = None
    # Also clear the provider binding: _user_count_for_provider counts by
    # oidc_provider_id alone, so an erased ghost would otherwise block that
    # provider's deletion forever (OIDC_PROVIDER_HAS_USERS).
    target.oidc_provider_id = None
    target.last_login_at = None
    db.flush()

    record_audit_event(
        db,
        event_type=AuditEventType.user_erased,
        actor_user_id=actor.id,
        target_type="user",
        target_id=target.id,
        metadata={
            "deleted_files": deleted_count,
            "deleted_bytes": deleted_bytes,
            "pii_purged": pii_purged,
        },
        request=request,
    )
    logger.info(
        "user erased: id=%d, %d files (%d bytes) hard-deleted",
        target.id,
        deleted_count,
        deleted_bytes,
    )
    return {
        "user_id": target.id,
        "deleted_files": deleted_count,
        "deleted_bytes": deleted_bytes,
        "erased_at": utc_now().isoformat(),
        # Same dict the audit row and the receipt PDF carry. Returned so a
        # caller can report what was purged without re-reading the audit log,
        # and so the residue is assertable in a test rather than only visible
        # in a metadata blob.
        "pii_purged": pii_purged,
    }


def compute_erasure_summary(db: Session, *, target: User) -> dict:
    """Pre-flight numbers shown in the admin's confirm dialog so they
    know exactly what's about to disappear before they hit the
    irreversible erase button."""
    from ..models.share import Share, ShareState
    from ..models.share_recipient import ShareRecipient

    files = (
        db.query(File)
        .filter(
            File.uploaded_by_id == target.id,
            File.state != FileState.deleted,
        )
        .all()
    )
    file_count = len(files)
    total_bytes = sum(f.size_bytes for f in files)

    # Shares this user created (will lose all of them).
    shares_created = (
        db.query(Share)
        .filter(Share.created_by_id == target.id)
        .count()
    )
    # Active shares this user is a recipient on (those rows survive but
    # the recipient label flips to "[erased]"); admin should know.
    shares_received = (
        db.query(Share)
        .join(ShareRecipient, ShareRecipient.share_id == Share.id)
        .filter(
            ShareRecipient.recipient_user_id == target.id,
            Share.state.in_(
                [ShareState.active, ShareState.expired, ShareState.revoked]
            ),
        )
        .count()
    )
    return {
        "user_id": target.id,
        "display_name": target.display_name,
        "email": target.email,
        "role": target.role.value,
        "is_already_erased": _is_erased(target),
        "files_to_delete": file_count,
        "bytes_to_delete": total_bytes,
        "shares_created": shares_created,
        "shares_received_to_anonymize": shares_received,
    }


def generate_receipt_pdf(audit_event) -> bytes:
    """Build a one-page PDF receipt for an erasure event. Used as a
    verifiable artifact the admin can hand back to the erased user.

    Dependencies on `reportlab` are kept inside this function so the
    module imports cheaply at startup."""
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title="fileHeron erasure receipt",
    )
    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    body_style = styles["BodyText"]

    extra = audit_event.extra or {}
    target_id = audit_event.target_id or "?"
    actor_id = audit_event.actor_user_id
    when = (
        audit_event.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        if audit_event.created_at
        else "?"
    )

    body = []
    body.append(Paragraph("file:Heron - Right-to-erasure receipt", title_style))
    body.append(Spacer(1, 4 * mm))
    body.append(
        Paragraph(
            "This document confirms that the user record below has been "
            "irreversibly erased from this fileHeron instance.",
            body_style,
        )
    )
    body.append(Spacer(1, 6 * mm))

    rows = [
        ["Erasure event ID", str(audit_event.id)],
        ["Erased user ID", str(target_id)],
        ["Performed by (admin user ID)", str(actor_id) if actor_id else "system"],
        ["When (UTC)", when],
        ["Request ID", str(audit_event.request_id or "n/a")],
        ["Files hard-deleted", str(extra.get("deleted_files", "0"))],
        ["Bytes hard-deleted", str(extra.get("deleted_bytes", "0"))],
    ]
    table = Table(rows, colWidths=[60 * mm, 110 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    body.append(table)
    body.append(Spacer(1, 8 * mm))
    body.append(
        Paragraph(
            "<i>Verifiability: this receipt's contents are derivable from "
            "the corresponding audit_log row at the time of generation. "
            "If the receipt and the audit row diverge, trust the audit row.</i>",
            body_style,
        )
    )
    doc.build(body)
    return buf.getvalue()

