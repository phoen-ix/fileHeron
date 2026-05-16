"""/api/admin/audit-log — list + streaming CSV export."""
from __future__ import annotations

import base64
import csv
import io
import json
from datetime import datetime
from typing import Iterator

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.audit_log import AuditLog
from ...models.user import User
from ...schemas.admin import AdminAuditResponse, AdminAuditRow

router = APIRouter()


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


def _audit_query(
    db: Session,
    *,
    event_type: str | None,
    actor_user_id: int | None,
    target_type: str | None,
    target_id: str | None,
    from_ts: datetime | None,
    to_ts: datetime | None,
):
    q = db.query(AuditLog)
    if event_type:
        q = q.filter(AuditLog.event_type == event_type)
    if actor_user_id is not None:
        q = q.filter(AuditLog.actor_user_id == actor_user_id)
    if target_type:
        q = q.filter(AuditLog.target_type == target_type)
    if target_id:
        q = q.filter(AuditLog.target_id == target_id)
    if from_ts:
        q = q.filter(AuditLog.created_at >= from_ts)
    if to_ts:
        q = q.filter(AuditLog.created_at <= to_ts)
    return q


@router.get("/audit-log", response_model=AdminAuditResponse)
def list_audit(
    event_type: str | None = Query(None),
    actor_user_id: int | None = Query(None),
    target_type: str | None = Query(None),
    target_id: str | None = Query(None),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdminAuditResponse:
    """Paginated audit-log feed.

    Two pagination modes coexist for back-compat:

    - **Cursor** (preferred when scrolling past page 1): pass `cursor`
      from the previous response's `next_cursor`. Filters apply against
      the (created_at, id) tuple so deep scans don't pre-walk every
      preceding row the way OFFSET does.
    - **Offset** (legacy): pass `page` and `page_size`. The new SPA
      uses cursor; curl scripts + the test suite still work with
      `?page=…`. The 1000-page cap stays as a DoS guard.

    Both paths return the same shape; `next_cursor` is populated on
    both so callers can switch over without a flag day.
    """
    q = _audit_query(
        db,
        event_type=event_type,
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        from_ts=from_ts,
        to_ts=to_ts,
    )

    if cursor is not None:
        ts, last_id = _decode_cursor(cursor)
        q = q.filter(
            or_(
                AuditLog.created_at < ts,
                and_(AuditLog.created_at == ts, AuditLog.id < last_id),
            )
        )
        total = 0  # counting "remaining below the cursor" is rarely worth the scan
        rows = (
            q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(page_size)
            .all()
        )
        effective_page = 1
    else:
        total = q.count()
        rows = (
            q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        effective_page = page
    # Bulk-load every distinct actor referenced on this page so the
    # SPA shows recognisable names instead of bare integer IDs. One
    # round-trip per page; misses (erased / unknown actors) leave the
    # new fields null and the SPA renders a small "(deleted)" tag.
    actor_ids = {r.actor_user_id for r in rows if r.actor_user_id is not None}
    actors_by_id: dict[int, User] = {}
    if actor_ids:
        for u in db.query(User).filter(User.id.in_(actor_ids)).all():
            actors_by_id[u.id] = u

    items: list[AdminAuditRow] = []
    for r in rows:
        actor = actors_by_id.get(r.actor_user_id) if r.actor_user_id else None
        items.append(
            AdminAuditRow(
                id=r.id,
                event_type=r.event_type,
                actor_user_id=r.actor_user_id,
                actor_display_name=actor.display_name if actor else None,
                actor_email=actor.email if actor else None,
                target_type=r.target_type,
                target_id=r.target_id,
                request_id=r.request_id,
                ip=r.ip,
                extra=r.extra,
                created_at=r.created_at,
            )
        )
    next_cursor: str | None = None
    if len(rows) == page_size and rows:
        last = rows[-1]
        next_cursor = _encode_cursor(last.created_at, last.id)

    return AdminAuditResponse(
        items=items,
        total=total,
        page=effective_page,
        page_size=page_size,
        next_cursor=next_cursor,
    )


@router.get("/audit-log/export.csv")
def export_audit_csv(
    event_type: str | None = Query(None),
    actor_user_id: int | None = Query(None),
    target_type: str | None = Query(None),
    target_id: str | None = Query(None),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> StreamingResponse:
    """Stream the full filter result as CSV. We yield batched rows so
    the response doesn't buffer the whole table in memory."""
    q = _audit_query(
        db,
        event_type=event_type,
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        from_ts=from_ts,
        to_ts=to_ts,
    ).order_by(AuditLog.created_at.desc())

    def _rows() -> Iterator[bytes]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "id",
                "created_at",
                "event_type",
                "actor_user_id",
                "target_type",
                "target_id",
                "ip",
                "request_id",
                "extra",
            ]
        )
        yield buf.getvalue().encode("utf-8")
        buf.seek(0)
        buf.truncate()

        BATCH = 500
        for r in q.yield_per(BATCH):
            writer.writerow(
                [
                    r.id,
                    r.created_at.isoformat() if r.created_at else "",
                    r.event_type,
                    r.actor_user_id if r.actor_user_id is not None else "",
                    r.target_type or "",
                    r.target_id or "",
                    r.ip or "",
                    r.request_id or "",
                    "" if r.extra is None else json.dumps(r.extra),
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
        "Content-Disposition": 'attachment; filename="fileheron-audit-log.csv"',
    }
    return StreamingResponse(_rows(), media_type="text/csv", headers=headers)
