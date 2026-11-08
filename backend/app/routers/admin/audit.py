"""/api/admin/audit-log - list + streaming CSV export."""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...models.audit_log import AuditEventType, AuditLog
from ...models.user import User
from ...schemas.admin import AdminAuditResponse, AdminAuditRow, AdminSettingsChangesResponse
from ...utils.csv_export import BATCH_SIZE, csv_response, csv_safe, decode_cursor, encode_cursor
from ...utils.timeutil import to_naive_utc

router = APIRouter()


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
    from_ts = to_naive_utc(from_ts)
    to_ts = to_naive_utc(to_ts)
    if from_ts:
        q = q.filter(AuditLog.created_at >= from_ts)
    if to_ts:
        q = q.filter(AuditLog.created_at <= to_ts)
    return q


def _hydrate_rows(db: Session, rows: list[AuditLog]) -> list[AdminAuditRow]:
    """Bulk-load every distinct actor referenced by `rows` so the SPA shows
    recognisable names instead of bare integer IDs. One round-trip; misses
    (erased / unknown actors) leave the fields null and the SPA renders a
    small "(deleted)" tag."""
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
    return items


# What the Overview's "Recently changed settings" panel lists. Settings
# changes are written as target_type="settings" (pinned by
# test_settings_change_links_pin.py); the two mail-test events share that
# target type without changing anything, and a task's schedule is a setting
# filed under target_type="cron".
_NOT_A_SETTINGS_CHANGE = (
    AuditEventType.smtp_test_foreign_target.value,
    AuditEventType.imap_test_foreign_target.value,
)
_ALSO_A_SETTINGS_CHANGE = (AuditEventType.cron_schedule_changed.value,)


@router.get("/audit-log/settings-changes", response_model=AdminSettingsChangesResponse)
def list_settings_changes(
    limit: int = Query(8, ge=1, le=50),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdminSettingsChangesResponse:
    """The newest settings changes, newest first. No COUNT: the panel shows a
    handful and links to the full log, and a count over audit_log on every
    Overview load would scan the table."""
    rows = (
        db.query(AuditLog)
        .filter(
            or_(
                and_(
                    AuditLog.target_type == "settings",
                    AuditLog.event_type.notin_(_NOT_A_SETTINGS_CHANGE),
                ),
                AuditLog.event_type.in_(_ALSO_A_SETTINGS_CHANGE),
            )
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .all()
    )
    return AdminSettingsChangesResponse(items=_hydrate_rows(db, rows))


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
        ts, last_id = decode_cursor(cursor)
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
    items = _hydrate_rows(db, rows)
    next_cursor: str | None = None
    if len(rows) == page_size and rows:
        last = rows[-1]
        next_cursor = encode_cursor(last.created_at, last.id)

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
    ).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())

    def _to_row(r) -> list:
        return [
            r.id,
            r.created_at.isoformat() if r.created_at else "",
            csv_safe(r.event_type),
            r.actor_user_id if r.actor_user_id is not None else "",
            csv_safe(r.target_type or ""),
            csv_safe(r.target_id or ""),
            csv_safe(r.ip or ""),
            csv_safe(r.request_id or ""),
            "" if r.extra is None else csv_safe(json.dumps(r.extra)),
        ]

    return csv_response(
        "fileheron-audit-log.csv",
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
        ],
        q.yield_per(BATCH_SIZE),
        _to_row,
    )
