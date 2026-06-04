"""/api/admin/sessions — cross-user session oversight (v1.7.0).

A session is a row in `refresh_tokens`. Admins list every user's sessions
(paginated, sortable, filterable), spot stale/hanging ones by sorting on
`last_used_at`, and revoke a single session or every session for one user.
All revokes are audited as `refresh_token_admin_revoked`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.audit_log import AuditEventType
from ...models.refresh_token import RefreshToken
from ...models.user import User
from ...schemas.admin import AdminSessionListResponse, AdminSessionRow
from ...services.audit import record_audit_event
from ...services.jwt_session import revoke_all_user_refresh_tokens

router = APIRouter()

_SORT_COLUMNS = {
    "created_at": RefreshToken.created_at,
    "last_used_at": RefreshToken.last_used_at,
    "expires_at": RefreshToken.expires_at,
}


def _utcnow_naive() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


@router.get("/sessions", response_model=AdminSessionListResponse)
def list_sessions(
    q: str | None = Query(None),
    user_id: int | None = Query(None),
    include_inactive: bool = Query(False),
    sort: Literal["created_at", "last_used_at", "expires_at"] = Query("last_used_at"),
    direction: Literal["asc", "desc"] = Query("asc"),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdminSessionListResponse:
    """Paginated session feed across all users.

    `include_inactive=false` (default) shows only live sessions
    (`revoked_at IS NULL AND expires_at > now`). Sorting defaults to
    `last_used_at asc` so the oldest/most-idle sessions surface first —
    the quickest way to spot stale or forgotten devices.
    """
    now = _utcnow_naive()
    base = db.query(RefreshToken)
    if not include_inactive:
        base = base.filter(
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
    if user_id is not None:
        base = base.filter(RefreshToken.user_id == user_id)
    if q:
        like = f"%{q.strip()}%"
        matching_user_ids = db.query(User.id).filter(
            or_(User.email.ilike(like), User.display_name.ilike(like))
        )
        base = base.filter(
            or_(
                RefreshToken.user_id.in_(matching_user_ids),
                RefreshToken.created_ip.ilike(like),
            )
        )

    total = base.count()
    col = _SORT_COLUMNS[sort]
    order = col.asc() if direction == "asc" else col.desc()
    rows = (
        base.order_by(order, RefreshToken.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    # Bulk-load every distinct owner on this page (one round-trip), mirroring
    # the audit-log actor hydration. Erased / unknown owners leave the name
    # fields null and the SPA renders a "(deleted)" tag.
    owner_ids = {r.user_id for r in rows}
    owners_by_id: dict[int, User] = {}
    if owner_ids:
        for u in db.query(User).filter(User.id.in_(owner_ids)).all():
            owners_by_id[u.id] = u

    items: list[AdminSessionRow] = []
    for r in rows:
        owner = owners_by_id.get(r.user_id)
        items.append(
            AdminSessionRow(
                id=r.id,
                user_id=r.user_id,
                user_display_name=owner.display_name if owner else None,
                user_email=owner.email if owner else None,
                created_at=r.created_at,
                last_used_at=r.last_used_at,
                expires_at=r.expires_at,
                revoked_at=r.revoked_at,
                created_ip=r.created_ip,
                created_ua=r.created_ua,
                is_active=(r.revoked_at is None and r.expires_at > now),
            )
        )

    return AdminSessionListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


@router.delete("/sessions/{session_id}")
def revoke_session(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict:
    """Soft-revoke a single session (any user). Idempotent on already-revoked
    rows. Audited as `refresh_token_admin_revoked`."""
    row = (
        db.query(RefreshToken)
        .filter(RefreshToken.id == session_id)
        .one_or_none()
    )
    if row is None:
        raise AppError(404, "SESSION_NOT_FOUND", "Session not found.")
    if row.revoked_at is None:
        row.revoked_at = _utcnow_naive()
    record_audit_event(
        db,
        event_type=AuditEventType.refresh_token_admin_revoked,
        actor_user_id=admin.id,
        target_type="refresh_token",
        target_id=row.id,
        metadata={"target_user_id": row.user_id, "reason": "admin_revoked"},
        request=request,
    )
    db.commit()
    return {"revoked": 1}


@router.delete("/users/{user_id}/sessions")
def revoke_user_sessions(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict:
    """Revoke every active session for one user. Audited with the count."""
    target = db.query(User).filter(User.id == user_id).one_or_none()
    if target is None:
        raise AppError(404, "USER_NOT_FOUND", "User not found.")
    revoked = revoke_all_user_refresh_tokens(db, user_id)
    record_audit_event(
        db,
        event_type=AuditEventType.refresh_token_admin_revoked,
        actor_user_id=admin.id,
        target_type="user",
        target_id=user_id,
        metadata={"count": revoked, "reason": "admin_revoked_all"},
        request=request,
    )
    db.commit()
    return {"revoked": int(revoked)}
