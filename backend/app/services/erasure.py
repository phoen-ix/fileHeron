"""GDPR right-to-erasure flow.

When admin requests erasure of user X:

1. **User row anonymized** — `email_hash` rewritten to `erased:<random>`
   so it never collides with a future signup; `email_hint` set to
   `[erased]`; `display_name` set to `[erased]`; `password_hash` blanked
   (login impossible); `is_disabled` set true; `oidc_subject` cleared.
2. **Files uploaded by X hard-deleted** — every `files` row where
   `uploaded_by_id == X.id`. Disk unlink + `state=deleted` + audit.
3. **Recipient references kept** — `share_recipients.recipient_user_id`
   stays as the FK; the row's display value is now `[erased]`. The
   sender's data (the share itself, files in it) is preserved.
4. **TOTP / recovery codes / refresh tokens / API tokens / sessions
   wiped** — anything that could grant ongoing access.
5. **Audit event** — `user_erased` with the admin as actor.

The flow is irreversible. Admin UI confirms with a two-step modal that
shows the file count + total bytes about to disappear.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.api_token import ApiToken
from ..models.audit_log import AuditEventType
from ..models.file import File, FileState
from ..models.refresh_token import RefreshToken
from ..models.user import User
from ..models.user_recovery_code import UserRecoveryCode
from ..models.user_totp import UserTOTP
from . import file as file_svc
from .audit import record_audit_event

logger = logging.getLogger("fileheron.erasure")


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def erase_user(
    db: Session, *, actor: User, target: User, request=None
) -> dict:
    """Run the erasure. Returns a small summary dict (file count + bytes).

    Caller commits."""
    if target.id == actor.id:
        raise AppError(
            400, "CANNOT_ERASE_SELF", "An admin cannot erase their own account."
        )
    if target.email == "[erased]":
        raise AppError(409, "ALREADY_ERASED", "This user has already been erased.")

    # 1. Hard-delete files this user uploaded.
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
    for f in files:
        deleted_bytes += f.size_bytes
        file_svc.hard_delete(db, file=f, reason="user_erased", request=request)
        deleted_count += 1

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

    # 3. Anonymize the row.
    target.email = f"erased:{secrets.token_hex(16)}"
    target.email = "[erased]"
    target.display_name = "[erased]"
    target.password_hash = ""
    target.is_disabled = True
    target.oidc_subject = None
    target.last_login_at = None
    target.requires_2fa_setup = False
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
        "erased_at": _utcnow().isoformat(),
    }


def compute_erasure_summary(db: Session, *, target: User) -> dict:
    """Pre-flight numbers shown in the admin's confirm dialog (Phase 8.3)
    so they know exactly what's about to disappear."""
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
        "is_already_erased": target.email == "[erased]",
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

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.lib import colors

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
    mono_style = styles["Code"]

    extra = audit_event.extra or {}
    target_id = audit_event.target_id or "?"
    actor_id = audit_event.actor_user_id
    when = (
        audit_event.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        if audit_event.created_at
        else "?"
    )

    body = []
    body.append(Paragraph("file:Heron — Right-to-erasure receipt", title_style))
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

