"""/api/notifications/* — per-user notification preferences + bell list/mark/stream.

Default channel is `both` for any category the user hasn't explicitly
set — that's resolved at read time in `services/notification.py`. We
only persist non-default rows, but for ergonomics the GET endpoint
returns the effective channel for every category (so the UI doesn't
have to know the defaults).

The /stream endpoint is SSE: long-lived HTTP connection that drains a
per-user Redis pubsub channel. Closes after 60s; the frontend is
expected to reconnect.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..dependencies import get_current_user, get_db
from ..middleware.errors import AppError
from ..models.notification import Notification, NotificationCategory
from ..models.user import User
from ..models.user_notification_preference import (
    NotificationChannel,
    UserNotificationPreference,
)
from ..schemas.notification import (
    MarkReadResponse,
    NotificationItem,
    NotificationListResponse,
    PreferenceItem,
    PreferencesResponse,
    UpdatePreferencesRequest,
)
from ..services import sse as sse_svc
from ..services import sse_token as sse_token_svc
from ..services.notification import _DEFAULT_CHANNEL  # internal, but stable

router = APIRouter(prefix="/api/notifications", tags=["notifications"])
# /stream lives on a separate router so it bypasses the global
# `require_2fa_complete` gate (which calls `get_actor`, which requires
# Authorization: Bearer — but EventSource can only send cookies/query).
# Auth is enforced inline in the endpoint via the signed-token path
# (browser) or the bearer path (curl).
stream_router = APIRouter(prefix="/api/notifications", tags=["notifications-stream"])


def _unread_count(db: Session, user_id: int) -> int:
    return (
        db.query(Notification)
        .filter(Notification.user_id == user_id, Notification.read_at.is_(None))
        .count()
    )


def _to_item(n: Notification) -> NotificationItem:
    return NotificationItem(
        id=n.id,
        category=n.category,
        payload=n.payload_json or {},
        link_url=n.link_url,
        created_at=n.created_at,
        read_at=n.read_at,
    )


# ---- Preferences ----------------------------------------------------------


@router.get("/preferences", response_model=PreferencesResponse)
def get_preferences(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PreferencesResponse:
    rows = (
        db.query(UserNotificationPreference)
        .filter(UserNotificationPreference.user_id == user.id)
        .all()
    )
    by_cat = {r.category: r.channel for r in rows}
    items = [
        PreferenceItem(
            category=cat,
            channel=by_cat.get(
                cat, _DEFAULT_CHANNEL.get(cat, NotificationChannel.both)
            ),
        )
        for cat in NotificationCategory
    ]
    return PreferencesResponse(items=items)


@router.put("/preferences", response_model=PreferencesResponse)
def update_preferences(
    payload: UpdatePreferencesRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PreferencesResponse:
    valid_cats = {c.value for c in NotificationCategory}
    valid_chans = {c.value for c in NotificationChannel}
    for cat_key, chan_val in payload.preferences.items():
        if cat_key not in valid_cats:
            raise AppError(
                400, "INVALID_CATEGORY", f"Unknown notification category: {cat_key}"
            )
        if chan_val not in valid_chans:
            raise AppError(
                400, "INVALID_CHANNEL", f"Unknown channel: {chan_val}"
            )

    for cat_key, chan_val in payload.preferences.items():
        cat = NotificationCategory(cat_key)
        chan = NotificationChannel(chan_val)
        existing = (
            db.query(UserNotificationPreference)
            .filter(
                UserNotificationPreference.user_id == user.id,
                UserNotificationPreference.category == cat,
            )
            .one_or_none()
        )
        if existing is None:
            db.add(
                UserNotificationPreference(
                    user_id=user.id, category=cat, channel=chan
                )
            )
        else:
            existing.channel = chan
    db.commit()
    return get_preferences(user=user, db=db)


# ---- List + read ---------------------------------------------------------


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    unread: bool = Query(False),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationListResponse:
    base = db.query(Notification).filter(Notification.user_id == user.id)
    if unread:
        base = base.filter(Notification.read_at.is_(None))
    total = base.count()
    rows = (
        base.order_by(Notification.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return NotificationListResponse(
        items=[_to_item(n) for n in rows],
        unread_count=_unread_count(db, user.id),
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("/{notif_id}/read", response_model=MarkReadResponse)
def mark_read(
    notif_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MarkReadResponse:
    from datetime import datetime, timezone

    n = (
        db.query(Notification)
        .filter(Notification.id == notif_id, Notification.user_id == user.id)
        .one_or_none()
    )
    if n is None:
        raise AppError(404, "NOTIFICATION_NOT_FOUND", "Notification not found.")
    if n.read_at is None:
        n.read_at = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        db.commit()
    return MarkReadResponse(ok=True, unread_count=_unread_count(db, user.id))


@router.post("/read-all", response_model=MarkReadResponse)
def mark_all_read(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MarkReadResponse:
    from datetime import datetime, timezone

    when = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    db.query(Notification).filter(
        Notification.user_id == user.id,
        Notification.read_at.is_(None),
    ).update({Notification.read_at: when}, synchronize_session=False)
    db.commit()
    return MarkReadResponse(ok=True, unread_count=0)


# ---- SSE stream ----------------------------------------------------------


@router.get("/stream-token")
def get_stream_token(user: User = Depends(get_current_user)) -> dict:
    """Mint a short-lived signed token the SPA passes to EventSource as
    ?token=<…>. EventSource cannot send Authorization headers; this is
    the workaround. Mirrors the /api/files/{id}/download-url pattern."""
    return {"token": sse_token_svc.issue(user.id)}


def _resolve_stream_user(
    request: Request,
    db: Session,
    token: str | None,
    authorization: str | None,
) -> User:
    """SSE auth: signed `?token=` (browser) or Authorization: Bearer
    (curl/CI). Mirrors `_resolve_download_user` in routers/files.py."""
    if token:
        user_id = sse_token_svc.verify(token)
        user = (
            db.query(User)
            .filter(User.id == user_id, User.is_disabled.is_(False))
            .one_or_none()
        )
        if user is None:
            raise AppError(401, "INVALID_SSE_TOKEN", "Bad SSE token.")
        request.state.user_id = user.id
        request.state.auth_via = "sse_token"
        return user

    # Bearer fallback for curl / API clients.
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError(401, "AUTH_REQUIRED", "Authentication required.")
    from ..services.auth import resolve_user_from_access_token

    jwt = authorization.split(" ", 1)[1].strip()
    user = resolve_user_from_access_token(db, jwt, settings)
    request.state.user_id = user.id
    request.state.auth_via = "session"
    return user


@stream_router.get("/stream")
async def stream(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = None,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    """Long-lived SSE connection. Per-user Redis pubsub channel; the
    server closes after 60s and the client is expected to reconnect.

    Two auth paths (see `_resolve_stream_user`):
    - `?token=<sse>` — short-lived HMAC, used by EventSource since it
      can't send Authorization headers.
    - `Authorization: Bearer <jwt>` — for curl/CI.

    Important Traefik / reverse-proxy headers below — see CLAUDE.md
    for the labels operators must NOT add (no buffering middleware)."""
    user = _resolve_stream_user(request, db, token, authorization)

    last_event_id_header = request.headers.get("last-event-id")
    last_event_id = None
    if last_event_id_header is not None:
        try:
            last_event_id = int(last_event_id_header)
        except ValueError:
            last_event_id = None

    return StreamingResponse(
        sse_svc.stream_for_user(user.id, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "Connection": "keep-alive",
        },
    )
