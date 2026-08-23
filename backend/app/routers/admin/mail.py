"""/api/admin/mail-log - outbound email log: list + detail + CSV + resend.

Mirrors routers/admin/audit.py (cursor/offset pagination, recipient
hydration, CSV-injection-safe streaming export). Bodies are deferred on
the model, so the list + CSV never load them - only the detail endpoint.
"""
from __future__ import annotations

import base64
import csv
import io
from collections.abc import Iterator
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.audit_log import AuditEventType
from ...models.email_log import EmailLog, EmailStatus, EmailVia
from ...models.user import User
from ...schemas.admin import (
    AdminMailDetail,
    AdminMailListResponse,
    AdminMailResendResponse,
    AdminMailRow,
)
from ...services import job_queue
from ...services.audit import record_audit_event
from ...utils.timeutil import to_naive_utc

router = APIRouter()

# Rows where the stored body had auth-link tokens redacted (or that were never
# real sends) can't be meaningfully re-sent.
_NO_RESEND_VIA = {EmailVia.test, EmailVia.dev_fallback}


def _can_resend(row: EmailLog) -> bool:
    return not row.masked and row.via not in _NO_RESEND_VIA


def _csv_safe(value) -> str:
    s = "" if value is None else str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def _encode_cursor(created_at: datetime, row_id: int) -> str:
    raw = f"{created_at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(s: str) -> tuple[datetime, int]:
    try:
        padded = s + "=" * (-len(s) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        ts_str, id_str = raw.split("|", 1)
        return datetime.fromisoformat(ts_str), int(id_str)
    except Exception as e:
        raise AppError(400, "INVALID_CURSOR", "Cursor is not valid.") from e


def _mail_query(
    db: Session,
    *,
    q: str | None,
    recipient_email: str | None,
    recipient_user_id: int | None,
    category: str | None,
    status: str | None,
    from_ts: datetime | None,
    to_ts: datetime | None,
):
    query = db.query(EmailLog)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(EmailLog.subject.ilike(like), EmailLog.recipient_email.ilike(like))
        )
    if recipient_email:
        query = query.filter(EmailLog.recipient_email.ilike(f"%{recipient_email}%"))
    if recipient_user_id is not None:
        query = query.filter(EmailLog.recipient_user_id == recipient_user_id)
    if category:
        query = query.filter(EmailLog.category == category)
    if status:
        query = query.filter(EmailLog.status == status)
    from_ts = to_naive_utc(from_ts)
    to_ts = to_naive_utc(to_ts)
    if from_ts:
        query = query.filter(EmailLog.created_at >= from_ts)
    if to_ts:
        query = query.filter(EmailLog.created_at <= to_ts)
    return query


def _row(r: EmailLog, display_name: str | None) -> AdminMailRow:
    return AdminMailRow(
        id=r.id,
        created_at=r.created_at,
        recipient_email=r.recipient_email,
        recipient_user_id=r.recipient_user_id,
        recipient_display_name=display_name,
        category=r.category,
        template_slug=r.template_slug,
        via=r.via.value,
        status=r.status.value,
        subject=r.subject,
        masked=r.masked,
        attempts=r.attempts,
        smtp_code=r.smtp_code,
        error_class=r.error_class,
        can_resend=_can_resend(r),
    )


@router.get("/mail-log", response_model=AdminMailListResponse)
def list_mail(
    q: str | None = Query(None),
    recipient_email: str | None = Query(None),
    recipient_user_id: int | None = Query(None),
    category: str | None = Query(None),
    status: str | None = Query(None),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdminMailListResponse:
    """Paginated mail-log feed. Cursor mode (preferred when scrolling) +
    offset mode (the SPA uses `?page=`), same shape as the audit log."""
    query = _mail_query(
        db,
        q=q,
        recipient_email=recipient_email,
        recipient_user_id=recipient_user_id,
        category=category,
        status=status,
        from_ts=from_ts,
        to_ts=to_ts,
    )

    if cursor is not None:
        ts, last_id = _decode_cursor(cursor)
        query = query.filter(
            or_(
                EmailLog.created_at < ts,
                and_(EmailLog.created_at == ts, EmailLog.id < last_id),
            )
        )
        total = 0
        rows = (
            query.order_by(EmailLog.created_at.desc(), EmailLog.id.desc())
            .limit(page_size)
            .all()
        )
        effective_page = 1
    else:
        total = query.count()
        rows = (
            query.order_by(EmailLog.created_at.desc(), EmailLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        effective_page = page

    user_ids = {r.recipient_user_id for r in rows if r.recipient_user_id is not None}
    names: dict[int, str] = {}
    if user_ids:
        for u in db.query(User).filter(User.id.in_(user_ids)).all():
            names[u.id] = u.display_name

    items = [
        _row(r, names.get(r.recipient_user_id) if r.recipient_user_id else None)
        for r in rows
    ]
    next_cursor: str | None = None
    if len(rows) == page_size and rows:
        last = rows[-1]
        next_cursor = _encode_cursor(last.created_at, last.id)

    return AdminMailListResponse(
        items=items,
        total=total,
        page=effective_page,
        page_size=page_size,
        next_cursor=next_cursor,
    )


@router.get("/mail-log/{log_id:int}", response_model=AdminMailDetail)
def get_mail(
    log_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdminMailDetail:
    """Full row including the (masked) bodies - the only endpoint that loads
    the deferred body columns."""
    row = db.get(EmailLog, log_id)
    if row is None:
        raise AppError(404, "MAIL_LOG_NOT_FOUND", "No such mail-log entry.")
    name = None
    if row.recipient_user_id is not None:
        u = db.get(User, row.recipient_user_id)
        name = u.display_name if u else None
    base = _row(row, name)
    return AdminMailDetail(
        **base.model_dump(),
        body_text=row.body_text,
        body_html=row.body_html,
        error_message=row.error_message,
        source_log_id=row.source_log_id,
    )


@router.post("/mail-log/{log_id:int}/resend", response_model=AdminMailResendResponse)
async def resend_mail(
    log_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> AdminMailResendResponse:
    """Re-enqueue a logged email to the same recipient. Refused for masked
    (auth-link) rows and for test/dev rows, whose stored body can't be
    meaningfully re-sent. Creates a fresh `via=resend` row linked to the
    original so its lifecycle is tracked independently."""
    orig = db.get(EmailLog, log_id)
    if orig is None:
        raise AppError(404, "MAIL_LOG_NOT_FOUND", "No such mail-log entry.")
    if not _can_resend(orig):
        raise AppError(
            409,
            "MAIL_RESEND_MASKED",
            "This email can't be resent: its one-time auth link was redacted "
            "at rest (or it was a test/dev entry).",
        )

    # The manage-subscriptions footer is redacted at rest but resend stays
    # allowed for it (the token only governs notification prefs). Re-sending the
    # stored body therefore delivered a live email whose "Manage subscriptions"
    # link read /manage-notifications/<redacted> - a dead link presented as a
    # working one. Mint a fresh token for the outgoing copy; the stored row
    # keeps the redacted form (audit 2026-07-30).
    from ...services import mail_log as mail_log_svc

    out_text = mail_log_svc.remint_footer(
        orig.body_text, user_id=orig.recipient_user_id
    )
    out_html = mail_log_svc.remint_footer(
        orig.body_html, user_id=orig.recipient_user_id
    )

    new_row = EmailLog(
        recipient_email=orig.recipient_email,
        recipient_user_id=orig.recipient_user_id,
        category=orig.category,
        template_slug=orig.template_slug,
        via=EmailVia.resend,
        status=EmailStatus.queued,
        subject=orig.subject,
        body_text=orig.body_text,
        body_html=orig.body_html,
        masked=orig.masked,
        attempts=0,
        source_log_id=orig.id,
    )
    db.add(new_row)
    db.flush()

    record_audit_event(
        db,
        event_type=AuditEventType.email_resent,
        actor_user_id=admin.id,
        target_type="email_log",
        target_id=str(orig.id),
        metadata={"new_log_id": new_row.id, "recipient": orig.recipient_email},
        request=request,
    )
    db.commit()

    # Async route → aenqueue so a Redis failure surfaces to the admin. The
    # worker finalizes the NEW row (not the original) via email_log_id.
    await job_queue.aenqueue(
        "send_email_job",
        to=new_row.recipient_email,
        subject=new_row.subject,
        text_body=out_text or "",
        html_body=out_html,
        email_log_id=new_row.id,
    )
    return AdminMailResendResponse(ok=True, new_log_id=new_row.id)


@router.get("/mail-log/export.csv")
def export_mail_csv(
    q: str | None = Query(None),
    recipient_email: str | None = Query(None),
    recipient_user_id: int | None = Query(None),
    category: str | None = Query(None),
    status: str | None = Query(None),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> StreamingResponse:
    """Stream the filtered result as CSV - metadata only, never bodies."""
    query = _mail_query(
        db,
        q=q,
        recipient_email=recipient_email,
        recipient_user_id=recipient_user_id,
        category=category,
        status=status,
        from_ts=from_ts,
        to_ts=to_ts,
    ).order_by(EmailLog.created_at.desc(), EmailLog.id.desc())

    def _rows() -> Iterator[bytes]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "id",
                "created_at",
                "recipient_email",
                "recipient_user_id",
                "category",
                "template_slug",
                "via",
                "status",
                "subject",
                "masked",
                "attempts",
                "smtp_code",
                "error_class",
            ]
        )
        yield buf.getvalue().encode("utf-8")
        buf.seek(0)
        buf.truncate()

        for r in query.yield_per(500):
            writer.writerow(
                [
                    r.id,
                    r.created_at.isoformat() if r.created_at else "",
                    _csv_safe(r.recipient_email),
                    r.recipient_user_id if r.recipient_user_id is not None else "",
                    _csv_safe(r.category or ""),
                    _csv_safe(r.template_slug or ""),
                    r.via.value,
                    r.status.value,
                    _csv_safe(r.subject),
                    "1" if r.masked else "0",
                    r.attempts,
                    r.smtp_code if r.smtp_code is not None else "",
                    _csv_safe(r.error_class or ""),
                ]
            )
            data = buf.getvalue()
            if len(data) > 8192:
                yield data.encode("utf-8")
                buf.seek(0)
                buf.truncate()
        tail = buf.getvalue()
        if tail:
            yield tail.encode("utf-8")

    headers = {
        "Content-Disposition": 'attachment; filename="fileheron-mail-log.csv"',
    }
    return StreamingResponse(_rows(), media_type="text/csv", headers=headers)
