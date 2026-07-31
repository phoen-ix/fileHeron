"""Admin file-inventory query.

A read-only cross-user view that lists every file ever uploaded -
including deleted/expired/quarantined ones - joined with its parent
share, the uploader, and aggregated download stats from
`download_log`. Designed to be paginated, sortable, and filterable
without N+1 queries.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..models.download_log import DownloadLog
from ..models.file import File, FileState
from ..models.group import Group
from ..models.share import Share, ShareState
from ..models.share_recipient import ShareRecipient
from ..models.user import User

VALID_SORT_COLUMNS = {
    "filename",
    "size",
    "state",
    "uploaded_at",
    "last_downloaded_at",
    "download_count",
}

# An orphan = bytes still on disk + counting quota, but the parent share is
# terminal. Excludes quarantine (infected, bytes in QUARANTINE_DIR).
_ORPHAN_FILE_STATES = (FileState.clean, FileState.ready_unscanned)
_ORPHAN_SHARE_STATES = (ShareState.revoked, ShareState.deleted)


def is_orphan(file: File, share: Share) -> bool:
    return file.state in _ORPHAN_FILE_STATES and share.state in _ORPHAN_SHARE_STATES


def _format_recipients(
    share_id: str,
    user_recipients: list[User],
    group_recipients: list[Group],
) -> str:
    """Compact, display-only label."""
    parts: list[str] = []
    for u in user_recipients[:1]:
        parts.append(f"{u.display_name} ({u.role.value})")
    for g in group_recipients[:1]:
        parts.append(f"{g.name} (group)")
    extra = (len(user_recipients) - 1 if user_recipients else 0) + (
        len(group_recipients) - 1 if group_recipients else 0
    )
    if extra > 0:
        parts.append(f"+{extra} more")
    if not parts:
        return "(none)"
    return ", ".join(parts)


def list_all_files(
    db: Session,
    *,
    q: str = "",
    state: str | None = None,
    uploader_id: int | None = None,
    share_state: str | None = None,
    orphaned: bool = False,
    include_inactive: bool = False,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    sort: str = "uploaded_at",
    direction: str = "desc",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict], int]:
    """Return (rows, total). Each row is a dict with all the fields
    needed for `AdminFileItem` plus pre-formatted recipients_summary -
    the route handler maps it into the schema."""
    # Aggregate download_log ONCE in its own grouped subselect instead of
    # grouping the whole join. The outer query then has no GROUP BY, which is
    # what let `total` degenerate into a COUNT over a grouped join of every
    # file against every download row.
    dl = (
        select(
            DownloadLog.file_id.label("file_id"),
            func.max(DownloadLog.accessed_at).label("last_dl"),
            func.count(DownloadLog.id).label("dl_count"),
        )
        .group_by(DownloadLog.file_id)
        .subquery()
    )

    filters = []

    if q:
        # Escape LIKE wildcards so a literal % or _ matches itself.
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{esc}%"
        filters.append(
            File.original_filename.ilike(like, escape="\\")
            | User.display_name.ilike(like, escape="\\")
            | User.email.ilike(like, escape="\\")
        )

    if state:
        valid = {s.value for s in FileState}
        if state in valid:
            filters.append(File.state == state)

    if share_state:
        valid_share = {s.value for s in ShareState}
        if share_state in valid_share:
            filters.append(Share.state == share_state)

    if orphaned:
        filters.append(File.state.in_(_ORPHAN_FILE_STATES))
        filters.append(Share.state.in_(_ORPHAN_SHARE_STATES))

    # Default view hides dead rows: deleted files + abandoned (failed-share)
    # uploads. Explicit state / share_state filters take precedence, so the
    # dropdowns can still surface them on demand.
    if not include_inactive:
        if not state:
            filters.append(File.state != FileState.deleted)
        if not share_state:
            filters.append(Share.state != ShareState.failed)

    if uploader_id is not None:
        filters.append(File.uploaded_by_id == uploader_id)

    if from_ts is not None:
        filters.append(File.created_at >= from_ts)
    if to_ts is not None:
        filters.append(File.created_at <= to_ts)

    def _joined(query):
        query = query.join(Share, Share.id == File.share_id).join(
            User, User.id == File.uploaded_by_id
        )
        for f in filters:
            query = query.filter(f)
        return query

    # Total without the limit/offset - and without the download join, which
    # contributes nothing to how many files match.
    total = _joined(db.query(func.count(File.id))).scalar() or 0

    base = _joined(
        db.query(File, Share, User, dl.c.last_dl, dl.c.dl_count)
    ).outerjoin(dl, dl.c.file_id == File.id)

    sort_col = sort if sort in VALID_SORT_COLUMNS else "uploaded_at"
    direction = direction if direction in ("asc", "desc") else "desc"

    column_map = {
        "filename": File.original_filename,
        "size": File.size_bytes,
        "state": File.state,
        "uploaded_at": File.created_at,
        "last_downloaded_at": dl.c.last_dl,
        "download_count": func.coalesce(dl.c.dl_count, 0),
    }
    target = column_map[sort_col]
    order = target.asc() if direction == "asc" else target.desc()
    base = base.order_by(order, desc(File.created_at))

    rows = base.offset((page - 1) * page_size).limit(page_size).all()

    # Bulk-load recipients for the page in one query.
    share_ids = [r[1].id for r in rows]
    recipient_rows = (
        db.query(ShareRecipient)
        .filter(ShareRecipient.share_id.in_(share_ids))
        .all()
        if share_ids
        else []
    )
    rec_user_ids: set[int] = set()
    rec_group_ids: set[int] = set()
    for rr in recipient_rows:
        if rr.recipient_user_id is not None:
            rec_user_ids.add(rr.recipient_user_id)
        if rr.recipient_group_id is not None:
            rec_group_ids.add(rr.recipient_group_id)
    users_by_id = (
        {u.id: u for u in db.query(User).filter(User.id.in_(rec_user_ids)).all()}
        if rec_user_ids
        else {}
    )
    groups_by_id = (
        {g.id: g for g in db.query(Group).filter(Group.id.in_(rec_group_ids)).all()}
        if rec_group_ids
        else {}
    )
    recips_by_share: dict[str, tuple[list[User], list[Group]]] = {
        sid: ([], []) for sid in share_ids
    }
    for rr in recipient_rows:
        if rr.recipient_user_id is not None:
            u = users_by_id.get(rr.recipient_user_id)
            if u is not None:
                recips_by_share[rr.share_id][0].append(u)
        elif rr.recipient_group_id is not None:
            g = groups_by_id.get(rr.recipient_group_id)
            if g is not None:
                recips_by_share[rr.share_id][1].append(g)

    out: list[dict] = []
    for f, s, uploader, last_dl, dl_count in rows:
        urs, grs = recips_by_share.get(s.id, ([], []))
        out.append(
            {
                "file_id": f.id,
                "filename": f.original_filename,
                "size_bytes": f.size_bytes,
                "state": f.state.value,
                "share_id": s.id,
                "share_subject": s.subject,
                "share_state": s.state.value,
                "uploader": {
                    "id": uploader.id,
                    "display_name": uploader.display_name,
                    "email": uploader.email,
                    "role": uploader.role.value,
                },
                "recipients_summary": _format_recipients(s.id, urs, grs),
                "uploaded_at": f.created_at,
                "last_downloaded_at": last_dl,
                "download_count": dl_count or 0,
                "is_orphaned": is_orphan(f, s),
            }
        )
    return out, total
