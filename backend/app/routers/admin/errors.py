"""/api/admin/error-log - browsable server-error log: list + detail + CSV.

Reads the ``error_log`` table the ``notify_admin_error`` worker writes (HTTP 5xx,
opted-in 4xx, failed crons). Offset pagination + filters (code / status / source
/ time range), mirroring routers/admin/mail.py. Read-only - error rows are never
mutated from here; retention is handled by the prune_history cron.
"""
from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.error_log import ErrorLog
from ...models.user import User
from ...schemas.admin import AdminErrorListResponse, AdminErrorRow
from ...services import error_log as error_log_svc

router = APIRouter()


def _csv_safe(value) -> str:
    s = "" if value is None else str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def _row(r: ErrorLog) -> AdminErrorRow:
    return AdminErrorRow(
        id=r.id,
        created_at=r.created_at,
        source=r.source,
        status_code=r.status_code,
        code=r.code,
        exception_type=r.exception_type,
        message=r.message,
        method=r.method,
        path=r.path,
        job_name=r.job_name,
        request_id=r.request_id,
        user_id=r.user_id,
        auth_via=r.auth_via,
        signature=r.signature,
        alerted=r.alerted,
    )


@router.get("/error-log", response_model=AdminErrorListResponse)
def list_errors(
    code: str | None = Query(None),
    status_code: int | None = Query(None, ge=100, le=599),
    source: str | None = Query(None),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdminErrorListResponse:
    """Paginated error-log feed, newest first, filterable by code/status/source/time."""
    rows, total = error_log_svc.list_errors(
        db,
        code=code,
        status_code=status_code,
        source=source,
        from_ts=from_ts,
        to_ts=to_ts,
        page=page,
        page_size=page_size,
    )
    return AdminErrorListResponse(
        items=[_row(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/error-log/{log_id:int}", response_model=AdminErrorRow)
def get_error(
    log_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdminErrorRow:
    row = error_log_svc.get(db, log_id)
    if row is None:
        raise AppError(404, "ERROR_LOG_NOT_FOUND", "No such error-log entry.")
    return _row(row)


@router.get("/error-log/export.csv")
def export_errors_csv(
    code: str | None = Query(None),
    status_code: int | None = Query(None, ge=100, le=599),
    source: str | None = Query(None),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> StreamingResponse:
    """Stream the filtered result as CSV."""
    query = error_log_svc.filtered_query(
        db, code=code, status_code=status_code, source=source, from_ts=from_ts, to_ts=to_ts
    ).order_by(ErrorLog.created_at.desc())

    def _rows() -> Iterator[bytes]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "id",
                "created_at",
                "source",
                "status_code",
                "code",
                "exception_type",
                "method",
                "path",
                "job_name",
                "request_id",
                "user_id",
                "auth_via",
                "signature",
                "alerted",
                "message",
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
                    _csv_safe(r.source),
                    r.status_code,
                    _csv_safe(r.code),
                    _csv_safe(r.exception_type or ""),
                    _csv_safe(r.method or ""),
                    _csv_safe(r.path or ""),
                    _csv_safe(r.job_name or ""),
                    _csv_safe(r.request_id or ""),
                    r.user_id if r.user_id is not None else "",
                    _csv_safe(r.auth_via or ""),
                    _csv_safe(r.signature),
                    "1" if r.alerted else "0",
                    _csv_safe(r.message or ""),
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
        "Content-Disposition": 'attachment; filename="fileheron-error-log.csv"',
    }
    return StreamingResponse(_rows(), media_type="text/csv", headers=headers)
