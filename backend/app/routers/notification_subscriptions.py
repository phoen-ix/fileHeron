"""/api/notification-subscriptions/* - anonymous, token-authed subscription
management.

Every email footer carries a signed manage-subscriptions token
(`services/unsubscribe_token.py`). These endpoints let the recipient view and
tune their notification preferences WITHOUT logging in - the token is the auth.
The action is low-risk (toggling the user's own notification channels), so the
token is long-lived and re-usable; it does not grant any other access.

The `one-click` endpoint implements RFC 8058 (List-Unsubscribe-Post): mail
clients POST `List-Unsubscribe=One-Click` to it to opt the recipient out with
a single tap, no page visit.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from ..dependencies import get_db
from ..middleware.errors import AppError
from ..models.user import User
from ..schemas.notification import (
    PreferenceItem,
    SubscriptionContextResponse,
    UnsubscribeRequest,
    UnsubscribeResponse,
    UpdatePreferencesRequest,
)
from ..services import notification_prefs, rate_limit
from ..services import unsubscribe_token as token_svc

router = APIRouter(
    prefix="/api/notification-subscriptions", tags=["notification-subscriptions"]
)


def _items(db: Session, user: User) -> list[PreferenceItem]:
    return [
        PreferenceItem.from_row(r)
        for r in notification_prefs.list_preferences(db, user)
    ]


def _resolve(db: Session, request: Request, token: str) -> User:
    """Rate-limit + verify the token + load the (enabled) user it belongs to."""
    ip = request.client.host if request.client else "unknown"
    if not rate_limit.check_ip_allowed("notif_subs", ip, limit=60, window_sec=60):
        raise AppError(429, "RATE_LIMITED", "Too many requests; slow down.")
    user_id = token_svc.verify(token)
    user = (
        db.query(User)
        .filter(User.id == user_id, User.is_disabled.is_(False))
        .one_or_none()
    )
    if user is None:
        raise AppError(401, "INVALID_MANAGE_TOKEN", "Bad manage link.")
    return user


@router.get("/{token}", response_model=SubscriptionContextResponse)
def get_subscriptions(
    token: str, request: Request, db: Session = Depends(get_db)
) -> SubscriptionContextResponse:
    user = _resolve(db, request, token)
    return SubscriptionContextResponse(
        display_name=user.display_name or "", items=_items(db, user)
    )


@router.put("/{token}", response_model=SubscriptionContextResponse)
def update_subscriptions(
    token: str,
    payload: UpdatePreferencesRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> SubscriptionContextResponse:
    user = _resolve(db, request, token)
    notification_prefs.update_preferences(db, user, payload.preferences)
    db.commit()
    return SubscriptionContextResponse(
        display_name=user.display_name or "", items=_items(db, user)
    )


@router.post("/{token}/unsubscribe", response_model=UnsubscribeResponse)
def unsubscribe(
    token: str,
    payload: UnsubscribeRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> UnsubscribeResponse:
    user = _resolve(db, request, token)
    prior = notification_prefs.unsubscribe_category(db, user, payload.category)
    db.commit()
    return UnsubscribeResponse(
        items=_items(db, user),
        category=payload.category,
        previous_channel=prior,
    )


@router.post("/{token}/one-click")
def one_click_unsubscribe(
    token: str,
    request: Request,
    category: str = Query(...),
    db: Session = Depends(get_db),
) -> Response:
    """RFC 8058 one-click endpoint. The mail client POSTs
    `List-Unsubscribe=One-Click` here; we ignore the body and opt the recipient
    out of `category`. Always 200 - a 4xx here just shows the recipient a
    confusing mail-client error - but the BODY says what actually happened."""
    user = _resolve(db, request, token)
    try:
        notification_prefs.unsubscribe_category(db, user, category)
        db.commit()
    except AppError as e:
        # A locked, operational or unknown category can't be one-click
        # unsubscribed. The footer emits no one-click URL for those, but an
        # email sent BEFORE that was true still carries one, so this is the
        # guard that actually holds. Don't claim success we didn't deliver.
        db.rollback()
        return Response(content=f"{e.message}\n", media_type="text/plain")
    return Response(content="Unsubscribed.\n", media_type="text/plain")
