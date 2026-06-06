"""Shared read/update layer for per-user notification preferences.

One place for the logic the authed prefs router (`routers/notifications.py`)
and the anonymous token-authed manage page (`routers/notification_subscriptions.py`)
both need:

- which categories are hidden for a given user (admin-only, or SSO when no
  provider is enabled),
- which are LOCKED - security-critical categories that can never be disabled,
  enforced server-side (not just greyed out in the UI),
- reading the effective preference list (defaults applied at read time),
- updating preferences (rejecting locked-category changes),
- a one-shot unsubscribe (set a category fully off),
- the effective delivery channel used by the dispatcher, where locked
  categories always resolve to their default regardless of any stored row.

Defaults live in `services/notification.py::_DEFAULT_CHANNEL` and are reused
here so there's a single source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.notification import ADMIN_ONLY_CATEGORIES, NotificationCategory
from ..models.user import User, UserRole
from ..models.user_notification_preference import (
    NotificationChannel,
    UserNotificationPreference,
)
from .notification import _DEFAULT_CHANNEL

# Security-critical categories. They appear in the manage UI (so the user knows
# they exist) but can never be switched off - disabling them would suppress
# account-recovery or account-takeover warnings. `reset_password` is sent via
# the direct auth path (never the dispatcher) so its row is purely informational;
# `login_alert` IS dispatched, so locking it here is what actually keeps
# new-device sign-in alerts always-on.
LOCKED_CATEGORIES: frozenset[NotificationCategory] = frozenset(
    {
        NotificationCategory.reset_password,
        NotificationCategory.login_alert,
    }
)


@dataclass(frozen=True)
class PrefRow:
    category: NotificationCategory
    channel: NotificationChannel
    locked: bool


def _default_channel(category: NotificationCategory) -> NotificationChannel:
    return _DEFAULT_CHANNEL.get(category, NotificationChannel.both)


def hidden_categories(db: Session, user: User) -> set[NotificationCategory]:
    """Categories that should not appear in this user's preferences because
    they can never deliver to them:
    - admin-only categories (ops/updates/inbound) for non-admins;
    - oidc_linked when no SSO provider is enabled (nobody can link SSO).
    """
    from . import oidc_admin

    hidden: set[NotificationCategory] = set()
    if user.role != UserRole.admin:
        hidden |= ADMIN_ONLY_CATEGORIES
    if not oidc_admin.is_any_enabled(db):
        hidden.add(NotificationCategory.oidc_linked)
    return hidden


def list_preferences(db: Session, user: User) -> list[PrefRow]:
    """Effective preference for every visible category (defaults applied at
    read time). Locked categories report their forced default channel."""
    rows = (
        db.query(UserNotificationPreference)
        .filter(UserNotificationPreference.user_id == user.id)
        .all()
    )
    by_cat = {r.category: r.channel for r in rows}
    hidden = hidden_categories(db, user)
    out: list[PrefRow] = []
    for cat in NotificationCategory:
        if cat in hidden:
            continue
        locked = cat in LOCKED_CATEGORIES
        channel = _default_channel(cat) if locked else by_cat.get(cat, _default_channel(cat))
        out.append(PrefRow(category=cat, channel=channel, locked=locked))
    return out


def _set(db: Session, user_id: int, category: NotificationCategory, channel: NotificationChannel) -> None:
    existing = (
        db.query(UserNotificationPreference)
        .filter(
            UserNotificationPreference.user_id == user_id,
            UserNotificationPreference.category == category,
        )
        .one_or_none()
    )
    if existing is None:
        db.add(
            UserNotificationPreference(user_id=user_id, category=category, channel=channel)
        )
    else:
        existing.channel = channel


def update_preferences(db: Session, user: User, prefs: dict[str, str]) -> None:
    """Validate + upsert. Rejects unknown/hidden categories, unknown channels,
    and any attempt to change a LOCKED category. Caller commits."""
    hidden = hidden_categories(db, user)
    valid_cats = {c.value for c in NotificationCategory if c not in hidden}
    valid_chans = {c.value for c in NotificationChannel}
    locked_vals = {c.value for c in LOCKED_CATEGORIES}

    for cat_key, chan_val in prefs.items():
        if cat_key not in valid_cats:
            raise AppError(400, "INVALID_CATEGORY", f"Unknown notification category: {cat_key}")
        if chan_val not in valid_chans:
            raise AppError(400, "INVALID_CHANNEL", f"Unknown channel: {chan_val}")
        if cat_key in locked_vals:
            raise AppError(
                400, "LOCKED_CATEGORY",
                "This notification is required for account security and cannot be disabled.",
            )

    for cat_key, chan_val in prefs.items():
        _set(db, user.id, NotificationCategory(cat_key), NotificationChannel(chan_val))


def unsubscribe_category(db: Session, user: User, category_value: str) -> NotificationChannel:
    """Turn one category fully off. Returns the prior effective channel so the
    caller can offer an Undo. Refuses hidden/locked categories. Caller commits."""
    hidden = hidden_categories(db, user)
    try:
        category = NotificationCategory(category_value)
    except ValueError:
        raise AppError(400, "INVALID_CATEGORY", f"Unknown notification category: {category_value}") from None
    if category in hidden:
        raise AppError(400, "INVALID_CATEGORY", f"Unknown notification category: {category_value}")
    if category in LOCKED_CATEGORIES:
        raise AppError(
            400, "LOCKED_CATEGORY",
            "This notification is required for account security and cannot be disabled.",
        )

    existing = (
        db.query(UserNotificationPreference)
        .filter(
            UserNotificationPreference.user_id == user.id,
            UserNotificationPreference.category == category,
        )
        .one_or_none()
    )
    prior = existing.channel if existing is not None else _default_channel(category)
    _set(db, user.id, category, NotificationChannel.off)
    return prior


def effective_channel(
    db: Session, user_id: int, category: NotificationCategory
) -> NotificationChannel:
    """Delivery channel the dispatcher should use. Locked categories ALWAYS
    resolve to their default channel, ignoring any stored row - so a user who
    previously turned a security alert off still receives it."""
    if category in LOCKED_CATEGORIES:
        return _default_channel(category)
    pref = (
        db.query(UserNotificationPreference)
        .filter(
            UserNotificationPreference.user_id == user_id,
            UserNotificationPreference.category == category,
        )
        .one_or_none()
    )
    if pref is not None:
        return pref.channel
    return _default_channel(category)
