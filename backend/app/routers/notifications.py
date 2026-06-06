"""/api/notifications/* - per-user notification preferences + bell list/mark/stream.

Default channel is `both` for any category the user hasn't explicitly
set - that's resolved at read time in `services/notification.py`. We
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
from ..models.notification import Notification
from ..models.user import User
from ..schemas.notification import (
    MarkReadResponse,
    NotificationItem,
    NotificationListResponse,
    PreferenceItem,
    PreferencesResponse,
    UpdatePreferencesRequest,
)
from ..services import notification_prefs, rate_limit
from ..services import sse as sse_svc
from ..services import sse_token as sse_token_svc

router = APIRouter(prefix="/api/notifications", tags=["notifications"])
# /stream lives on a separate router so it bypasses the global
# `require_2fa_complete` gate (which calls `get_actor`, which requires
# Authorization: Bearer - but EventSource can only send cookies/query).
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


def _prefs_response(db: Session, user: User) -> PreferencesResponse:
    return PreferencesResponse(
        items=[
            PreferenceItem(category=r.category, channel=r.channel, locked=r.locked)
            for r in notification_prefs.list_preferences(db, user)
        ]
    )


@router.get("/preferences", response_model=PreferencesResponse)
def get_preferences(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PreferencesResponse:
    return _prefs_response(db, user)


@router.put("/preferences", response_model=PreferencesResponse)
def update_preferences(
    payload: UpdatePreferencesRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PreferencesResponse:
    notification_prefs.update_preferences(db, user, payload.preferences)
    db.commit()
    return _prefs_response(db, user)


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


@router.delete("/{notif_id}", response_model=MarkReadResponse)
def delete_one(
    notif_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MarkReadResponse:
    """Hard-delete a single notification. The bell is a delete-to-dismiss inbox
    (the read/unread concept is retired); clicking an item removes it."""
    deleted = (
        db.query(Notification)
        .filter(Notification.id == notif_id, Notification.user_id == user.id)
        .delete(synchronize_session=False)
    )
    if deleted == 0:
        raise AppError(404, "NOTIFICATION_NOT_FOUND", "Notification not found.")
    db.commit()
    # `unread_count` is the post-delete count of the user's notifications
    # (read_at is no longer set, so this is simply how many remain).
    return MarkReadResponse(ok=True, unread_count=_unread_count(db, user.id))


@router.delete("", response_model=MarkReadResponse)
def delete_all(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MarkReadResponse:
    """Hard-delete all of the caller's notifications ("Delete all")."""
    db.query(Notification).filter(
        Notification.user_id == user.id,
    ).delete(synchronize_session=False)
    db.commit()
    return MarkReadResponse(ok=True, unread_count=0)


# ---- SSE stream ----------------------------------------------------------


@router.get("/stream-token")
def get_stream_token(
    request: Request, user: User = Depends(get_current_user)
) -> dict:
    """Mint a short-lived signed token the SPA passes to EventSource as
    ?token=<…>. EventSource cannot send Authorization headers; this is
    the workaround. Mirrors the /api/files/{id}/download-url pattern.

    Generous per-IP backstop so a token-mint flood can't be used to
    amplify the stream DoS (the per-user connection cap in services/sse
    is the primary bound)."""
    ip = request.client.host if request.client else "unknown"
    if not rate_limit.check_ip_allowed("sse_token", ip, limit=120, window_sec=60):
        raise AppError(429, "RATE_LIMITED", "Too many requests; slow down.")
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
    - `?token=<sse>` - short-lived HMAC, used by EventSource since it
      can't send Authorization headers.
    - `Authorization: Bearer <jwt>` - for curl/CI.

    Important Traefik / reverse-proxy headers below - see CLAUDE.md
    for the labels operators must NOT add (no buffering middleware)."""
    user = _resolve_stream_user(request, db, token, authorization)

    last_event_id_header = request.headers.get("last-event-id")
    last_event_id = None
    if last_event_id_header is not None:
        try:
            last_event_id = int(last_event_id_header)
        except ValueError:
            last_event_id = None

    # Per-user concurrent-connection cap (finding M4). Acquire here so we
    # can return a clean 429; release in the generator's finally, which
    # Starlette invokes on completion OR client disconnect.
    if not sse_svc.try_acquire_user_stream(user.id):
        raise AppError(
            429, "TOO_MANY_STREAMS", "Too many concurrent connections; close some tabs."
        )

    async def _capped_stream():
        try:
            async for frame in sse_svc.stream_for_user(user.id, last_event_id):
                yield frame
        finally:
            sse_svc.release_user_stream(user.id)

    return StreamingResponse(
        _capped_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "Connection": "keep-alive",
        },
    )
