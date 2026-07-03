"""Admin analytics: a tiny daily storage snapshot + live aggregation.

Design (see plan Thread 3): storage/file-state over time is the ONLY thing that
can't be reconstructed after deletes, so a nightly cron writes one row/day into
`analytics_snapshots`. Everything else - share activity, downloads, AV events,
top-N, quota warnings - is computed live here from persisted timestamps and
current state, so it's always fresh (no staleness, no cron dependency).

Day-bucketing uses ``func.date(...)`` which is portable across SQLite (tests)
and MariaDB (prod); ``str(row[0])[:10]`` normalises the bucket key on both.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.analytics_snapshot import AnalyticsSnapshot
from ..models.audit_log import AuditEventType, AuditLog
from ..models.download_log import DownloadLog
from ..models.file import File, FileState
from ..models.share import Share
from ..models.user import User
from ..utils.timeutil import utc_now
from . import quota as quota_svc

# Files that count as "stored" (in-flight + finalized, not deleted). Mirrors
# quota._used_bytes_query so storage totals match the quota figures.
_STORED_STATES = [FileState.uploading, FileState.ready_unscanned, FileState.clean]


def snapshot_storage_today(db: Session) -> AnalyticsSnapshot:
    """Upsert today's storage/file-state row. Idempotent on `snapshot_date`
    (a re-run overwrites today). Caller commits."""
    today = utc_now().date()
    storage = int(
        db.query(func.coalesce(func.sum(File.size_bytes), 0))
        .filter(File.state.in_(_STORED_STATES))
        .scalar()
        or 0
    )
    by_state = dict(
        db.query(File.state, func.count(File.id)).group_by(File.state).all()
    )

    def _n(state: FileState) -> int:
        return int(by_state.get(state, 0) or 0)

    total = sum(int(v or 0) for k, v in by_state.items() if k != FileState.deleted)

    row = (
        db.query(AnalyticsSnapshot)
        .filter(AnalyticsSnapshot.snapshot_date == today)
        .one_or_none()
    )
    if row is None:
        row = AnalyticsSnapshot(snapshot_date=today)
        db.add(row)
    row.storage_bytes = storage
    row.files_clean = _n(FileState.clean)
    row.files_infected = _n(FileState.infected)
    row.files_total = total
    db.flush()
    return row


def _cutoff(days: int) -> tuple[date, datetime]:
    start_date = utc_now().date() - timedelta(days=days - 1)
    return start_date, datetime.combine(start_date, time.min)


def _zero_filled(counts: dict[str, int], start: date, days: int) -> list[dict]:
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        out.append({"date": d, "count": int(counts.get(d, 0))})
    return out


def _daily(db, date_col, *filters, start_dt: datetime) -> dict[str, int]:
    bucket = func.date(date_col)
    rows = (
        db.query(bucket, func.count())
        .filter(date_col >= start_dt, *filters)
        .group_by(bucket)
        .all()
    )
    return {str(r[0])[:10]: int(r[1]) for r in rows}


def compute_analytics(db: Session, days: int = 30) -> dict:
    """The live analytics bundle for the last `days` days."""
    start_date, start_dt = _cutoff(days)

    # Storage trend - from the daily snapshots (the only non-reconstructable bit).
    snaps = (
        db.query(AnalyticsSnapshot)
        .filter(AnalyticsSnapshot.snapshot_date >= start_date)
        .order_by(AnalyticsSnapshot.snapshot_date.asc())
        .all()
    )
    storage_trend = [
        {
            "date": s.snapshot_date.isoformat(),
            "storage_bytes": int(s.storage_bytes),
            "files_clean": s.files_clean,
            "files_infected": s.files_infected,
            "files_total": s.files_total,
        }
        for s in snaps
    ]

    # Daily time-series (reconstructed live).
    shares_created = _zero_filled(
        _daily(db, Share.created_at, start_dt=start_dt), start_date, days
    )
    downloads = _zero_filled(
        _daily(db, DownloadLog.accessed_at, start_dt=start_dt), start_date, days
    )
    av_quarantines = _zero_filled(
        _daily(
            db,
            AuditLog.created_at,
            AuditLog.event_type == AuditEventType.file_quarantined.value,
            start_dt=start_dt,
        ),
        start_date,
        days,
    )

    # Current file-state breakdown.
    file_states = {
        (k.value if isinstance(k, FileState) else str(k)): int(v or 0)
        for k, v in db.query(File.state, func.count(File.id)).group_by(File.state).all()
    }

    # Top uploaders by stored bytes (exclude GDPR-erased rows).
    top_uploaders = [
        {"user_id": uid, "display_name": name, "email": email, "bytes": int(b or 0)}
        for uid, name, email, b in (
            db.query(
                User.id,
                User.display_name,
                User.email,
                func.coalesce(func.sum(File.size_bytes), 0),
            )
            .join(File, File.uploaded_by_id == User.id)
            .filter(
                File.state.in_(_STORED_STATES),
                ~User.email.like("erased-%@erased.invalid"),
            )
            .group_by(User.id, User.display_name, User.email)
            .order_by(func.coalesce(func.sum(File.size_bytes), 0).desc())
            .limit(10)
            .all()
        )
    ]

    # Top shares by download count.
    top_shares = [
        {"share_id": sid, "subject": subject, "downloads": int(c or 0)}
        for sid, subject, c in (
            db.query(Share.id, Share.subject, func.count(DownloadLog.id))
            .join(DownloadLog, DownloadLog.share_id == Share.id)
            .filter(DownloadLog.accessed_at >= start_dt)
            .group_by(Share.id, Share.subject)
            .order_by(func.count(DownloadLog.id).desc())
            .limit(10)
            .all()
        )
    ]

    # Quota warnings - users over 90% of a set quota.
    quota_users = db.query(User).filter(User.quota_bytes.isnot(None)).all()
    used = quota_svc.storage_used_bytes_bulk(db, [u.id for u in quota_users])
    quota_warnings = []
    for u in quota_users:
        limit = u.quota_bytes or 0
        if limit <= 0:
            continue
        used_b = used.get(u.id, 0)
        if used_b / limit > 0.9:
            quota_warnings.append(
                {
                    "user_id": u.id,
                    "display_name": u.display_name,
                    "email": u.email,
                    "used_bytes": used_b,
                    "quota_bytes": limit,
                    "pct": round(used_b / limit * 100, 1),
                }
            )
    quota_warnings.sort(key=lambda w: w["pct"], reverse=True)

    return {
        "days": days,
        "range": {"from": start_date.isoformat(), "to": utc_now().date().isoformat()},
        "storage_trend": storage_trend,
        "storage_as_of": storage_trend[-1]["date"] if storage_trend else None,
        "shares_created": shares_created,
        "downloads": downloads,
        "av_quarantines": av_quarantines,
        "file_states": file_states,
        "top_uploaders": top_uploaders,
        "top_shares": top_shares,
        "quota_warnings": quota_warnings,
    }
