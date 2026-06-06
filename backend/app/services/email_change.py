"""Email-change orchestrator (v1.13.0).

One place for the request → (stage token[s]) → confirm → apply flow, shared by
the admin endpoint, the self-service endpoint, and the public confirm/cancel
endpoints. The policy (verification mode + OIDC behaviour) is read live from
``email_change_policy`` and *frozen* onto the pending row so a mid-flight
settings change can't alter an in-progress confirmation.

The shared ``_apply_email_change`` is the only place ``users.email`` is
mutated. It always lands the new address with ``email_verified=True`` — in the
verified modes because control was just proven, in ``immediate`` because the
admin is trusted — so the email-verified login gate is never tripped (which
would otherwise lock the user out).

Services do the DB work; routers do the awaited email sends *after commit*.
Each public function therefore returns a small outcome object carrying the
plaintext tokens + flags the router needs to dispatch the right emails.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.email_change_token import EmailChangeToken
from ..models.user import Locale, User
from ..utils.crypto import normalize_email, random_token, sha256_hex
from ..utils.timeutil import utc_now
from . import email_change_policy
from . import oidc as oidc_svc
from . import user_management as um_svc
from .audit import record_audit_event
from .jwt_session import revoke_all_user_refresh_tokens

logger = logging.getLogger("fileheron.email_change")

EMAIL_CHANGE_TTL = timedelta(hours=24)

_ERASED_DOMAIN = "@erased.invalid"


@dataclass
class RequestOutcome:
    """What ``request_email_change`` decided. The router maps this to sends."""

    mode: str  # 'immediate' | 'verify_new' | 'verify_both'
    applied: bool  # True ⇒ change is already live (immediate / skip_verification)
    user_id: int
    new_email: str
    old_email: str
    locale: Locale
    display_name: str
    by_admin: bool
    # Pending-mode plaintext tokens (None when applied immediately):
    new_token: str | None = None
    old_token: str | None = None  # verify_both only
    cancel_token: str | None = None  # pending modes
    expires_at: datetime | None = None
    # Applied-immediately payload:
    oidc_reset: bool = False
    set_password_token: str | None = None


@dataclass
class ConfirmOutcome:
    """What ``confirm_email_change`` decided after marking one side confirmed."""

    applied: bool
    pending_side: str | None = None  # 'old' / 'new' still missing when not applied
    user_id: int | None = None
    new_email: str | None = None
    old_email: str | None = None
    locale: Locale | None = None
    display_name: str | None = None
    by_admin: bool = False
    oidc_reset: bool = False
    set_password_token: str | None = None
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_erased(user: User) -> bool:
    return (user.email or "").endswith(_ERASED_DOMAIN) or user.display_name == "[erased]"


def _assert_email_available(db: Session, em: str, *, exclude_user_id: int) -> None:
    taken = (
        db.query(User.id)
        .filter(User.email == em, User.id != exclude_user_id)
        .first()
    )
    if taken is not None:
        raise AppError(409, "EMAIL_TAKEN", "That email address is already in use.")


def _supersede_pending(db: Session, *, user_id: int) -> int:
    """Cancel any still-live pending change for this user so only the latest
    request's link(s) can confirm. Returns the count superseded."""
    rows = (
        db.query(EmailChangeToken)
        .filter(
            EmailChangeToken.user_id == user_id,
            EmailChangeToken.used_at.is_(None),
            EmailChangeToken.cancelled_at.is_(None),
        )
        .all()
    )
    now = utc_now()
    for r in rows:
        r.cancelled_at = now
    if rows:
        db.flush()
    return len(rows)


def _apply_email_change(
    db: Session,
    *,
    user: User,
    new_email: str,
    initiated_by: User,
    oidc_mode: str,
    request: Request | None,
    via: str,
) -> tuple[bool, str | None]:
    """The single mutation point. Returns (oidc_reset, set_password_token)."""
    _assert_email_available(db, new_email, exclude_user_id=user.id)
    old_email = user.email
    user.email = new_email
    user.email_verified = True
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "EMAIL_TAKEN", "That email address is already in use.") from None

    oidc_reset = False
    set_password_token: str | None = None
    if user.oidc_provider_id and oidc_mode in ("reset_setpw", "reset_only"):
        oidc_svc.unlink(db, user=user, request=request)
        oidc_reset = True
        if oidc_mode == "reset_setpw":
            set_password_token = um_svc.force_password_reset(
                db, actor=initiated_by, target=user, request=request
            )

    # An email change is an identity-sensitive event (same posture as a
    # password reset): drop every refresh session so the next access is a
    # deliberate re-login under the new identity.
    revoke_all_user_refresh_tokens(db, user.id)

    record_audit_event(
        db,
        event_type=AuditEventType.email_changed,
        actor_user_id=initiated_by.id,
        target_type="user",
        target_id=str(user.id),
        metadata={
            "old_email": old_email,
            "new_email": new_email,
            "self": initiated_by.id == user.id,
            "oidc_reset": oidc_reset,
            "via": via,
        },
        request=request,
    )
    db.flush()
    return oidc_reset, set_password_token


# ---------------------------------------------------------------------------
# Public flow
# ---------------------------------------------------------------------------


def request_email_change(
    db: Session,
    *,
    target: User,
    new_email: str,
    initiated_by: User,
    request: Request | None,
    skip_verification: bool = False,
) -> RequestOutcome:
    """Validate + either apply immediately (immediate mode / admin skip) or
    stage a pending change with the right token(s). Caller commits, then sends
    the emails the outcome describes."""
    em = normalize_email(new_email)
    if not em:
        raise AppError(400, "INVALID_EMAIL", "Email cannot be empty.")
    if _is_erased(target):
        raise AppError(409, "USER_ERASED", "Cannot change the email of an erased user.")
    if em == target.email:
        raise AppError(409, "EMAIL_UNCHANGED", "That is already this account's email.")
    _assert_email_available(db, em, exclude_user_id=target.id)

    by_admin = initiated_by.id != target.id
    mode = email_change_policy.effective_verification_mode(db)
    oidc_mode = email_change_policy.effective_oidc_mode(db)
    old_email = target.email

    if skip_verification or mode == "immediate":
        oidc_reset, set_pw = _apply_email_change(
            db,
            user=target,
            new_email=em,
            initiated_by=initiated_by,
            oidc_mode=oidc_mode,
            request=request,
            via="immediate",
        )
        return RequestOutcome(
            mode="immediate",
            applied=True,
            user_id=target.id,
            new_email=em,
            old_email=old_email,
            locale=target.locale,
            display_name=target.display_name,
            by_admin=by_admin,
            oidc_reset=oidc_reset,
            set_password_token=set_pw,
        )

    _supersede_pending(db, user_id=target.id)

    new_plain = random_token(32)
    cancel_plain = random_token(32)
    old_plain = random_token(32) if mode == "verify_both" else None

    record = EmailChangeToken(
        user_id=target.id,
        new_email=em,
        new_token_hash=sha256_hex(new_plain),
        old_token_hash=sha256_hex(old_plain) if old_plain else None,
        cancel_token_hash=sha256_hex(cancel_plain),
        oidc_mode=oidc_mode,
        initiated_by_id=initiated_by.id,
        expires_at=utc_now() + EMAIL_CHANGE_TTL,
    )
    db.add(record)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.email_change_requested,
        actor_user_id=initiated_by.id,
        target_type="user",
        target_id=str(target.id),
        metadata={
            "new_email": em,
            "mode": mode,
            "self": not by_admin,
            "oidc_mode": oidc_mode,
        },
        request=request,
    )
    return RequestOutcome(
        mode=mode,
        applied=False,
        user_id=target.id,
        new_email=em,
        old_email=old_email,
        locale=target.locale,
        display_name=target.display_name,
        by_admin=by_admin,
        new_token=new_plain,
        old_token=old_plain,
        cancel_token=cancel_plain,
        expires_at=record.expires_at,
    )


def confirm_email_change(
    db: Session, *, token: str, request: Request | None
) -> ConfirmOutcome:
    """Confirm one side (new or old). Applies the change once all required
    sides are confirmed. Caller commits."""
    h = sha256_hex(token)
    record = (
        db.query(EmailChangeToken)
        .filter(
            or_(
                EmailChangeToken.new_token_hash == h,
                EmailChangeToken.old_token_hash == h,
            )
        )
        .one_or_none()
    )
    if record is None:
        raise AppError(404, "EMAIL_CHANGE_TOKEN_INVALID", "This link is invalid.")
    if record.used_at is not None:
        raise AppError(410, "EMAIL_CHANGE_TOKEN_USED", "This link has already been used.")
    if record.cancelled_at is not None:
        raise AppError(
            410, "EMAIL_CHANGE_TOKEN_CANCELLED", "This email change was cancelled."
        )
    if record.expires_at < utc_now():
        raise AppError(410, "EMAIL_CHANGE_TOKEN_EXPIRED", "This link has expired.")

    side = "new" if record.new_token_hash == h else "old"
    col = (
        EmailChangeToken.new_confirmed_at
        if side == "new"
        else EmailChangeToken.old_confirmed_at
    )
    # Atomic single-use claim of THIS side (mirrors consume_password_reset).
    claimed = db.execute(
        update(EmailChangeToken)
        .where(
            EmailChangeToken.id == record.id,
            col.is_(None),
            EmailChangeToken.used_at.is_(None),
            EmailChangeToken.cancelled_at.is_(None),
        )
        .values({col: utc_now()})
    )
    if claimed.rowcount == 0:
        raise AppError(410, "EMAIL_CHANGE_TOKEN_USED", "This link has already been used.")
    db.flush()
    db.refresh(record)

    new_ok = record.new_confirmed_at is not None
    old_ok = (not record.requires_old) or (record.old_confirmed_at is not None)
    if not (new_ok and old_ok):
        missing = "new" if not new_ok else "old"
        return ConfirmOutcome(applied=False, pending_side=missing)

    user = db.query(User).filter(User.id == record.user_id).one_or_none()
    if user is None:
        raise AppError(404, "EMAIL_CHANGE_TOKEN_INVALID", "This link is invalid.")
    old_email = user.email
    by_admin = (
        record.initiated_by_id is not None and record.initiated_by_id != user.id
    )
    initiator = (
        db.query(User).filter(User.id == record.initiated_by_id).one_or_none()
        if record.initiated_by_id is not None
        else None
    )
    actor = initiator or user
    oidc_reset, set_pw = _apply_email_change(
        db,
        user=user,
        new_email=record.new_email,
        initiated_by=actor,
        oidc_mode=record.oidc_mode,
        request=request,
        via="confirm",
    )
    record.used_at = utc_now()
    db.flush()
    return ConfirmOutcome(
        applied=True,
        user_id=user.id,
        new_email=record.new_email,
        old_email=old_email,
        locale=user.locale,
        display_name=user.display_name,
        by_admin=by_admin,
        oidc_reset=oidc_reset,
        set_password_token=set_pw,
    )


def cancel_email_change(
    db: Session,
    *,
    token: str | None = None,
    user: User | None = None,
    request: Request | None = None,
) -> int:
    """Invalidate pending change(s). ``token`` = old-email "it wasn't me"
    kill switch; ``user`` = self/admin revoke of every live pending change.
    Returns the count cancelled. Caller commits."""
    base = db.query(EmailChangeToken).filter(
        EmailChangeToken.used_at.is_(None),
        EmailChangeToken.cancelled_at.is_(None),
    )
    if token is not None:
        record = base.filter(
            EmailChangeToken.cancel_token_hash == sha256_hex(token)
        ).one_or_none()
        if record is None:
            raise AppError(
                404,
                "EMAIL_CHANGE_TOKEN_INVALID",
                "This link is invalid or the change was already completed.",
            )
        if record.expires_at < utc_now():
            raise AppError(410, "EMAIL_CHANGE_TOKEN_EXPIRED", "This link has expired.")
        record.cancelled_at = utc_now()
        db.flush()
        record_audit_event(
            db,
            event_type=AuditEventType.email_change_cancelled,
            actor_user_id=record.user_id,
            target_type="user",
            target_id=str(record.user_id),
            metadata={"via": "cancel_link"},
            request=request,
        )
        return 1

    if user is not None:
        rows = base.filter(EmailChangeToken.user_id == user.id).all()
        now = utc_now()
        for r in rows:
            r.cancelled_at = now
        if rows:
            db.flush()
            record_audit_event(
                db,
                event_type=AuditEventType.email_change_cancelled,
                actor_user_id=user.id,
                target_type="user",
                target_id=str(user.id),
                metadata={"via": "user_revoke", "count": len(rows)},
                request=request,
            )
        return len(rows)

    return 0


# ---------------------------------------------------------------------------
# Post-commit email dispatch (called by routers AFTER db.commit()). Each is
# wrapped so a dead SMTP / bouncing address can't 500 an already-persisted
# change.
# ---------------------------------------------------------------------------


async def dispatch_request_emails(db: Session, outcome: RequestOutcome) -> None:
    from . import email as email_svc
    from . import site as site_svc

    base = site_svc.get_site_url(db)
    tz = site_svc.get_site_timezone(db)
    loc = outcome.locale
    name = outcome.display_name
    try:
        if outcome.applied:
            await email_svc.send_email_change_alert(
                to=outcome.old_email, locale=loc, display_name=name,
                new_email=outcome.new_email, by_admin=outcome.by_admin,
                applied=True, app_url=base, site_timezone=tz,
            )
            if outcome.set_password_token:
                await email_svc.send_password_reset_email(
                    to=outcome.new_email, locale=loc, display_name=name,
                    token=outcome.set_password_token, app_url=base, site_timezone=tz,
                )
            await email_svc.send_email_change_completed(
                to=outcome.new_email, locale=loc, display_name=name,
                new_email=outcome.new_email, oidc_reset=outcome.oidc_reset,
                app_url=base, site_timezone=tz,
            )
        else:
            await email_svc.send_email_change_confirm(
                to=outcome.new_email, locale=loc, display_name=name,
                token=outcome.new_token, new_email=outcome.new_email,
                by_admin=outcome.by_admin, app_url=base, site_timezone=tz,
            )
            if outcome.mode == "verify_both" and outcome.old_token:
                await email_svc.send_email_change_verify_old(
                    to=outcome.old_email, locale=loc, display_name=name,
                    confirm_token=outcome.old_token, cancel_token=outcome.cancel_token,
                    new_email=outcome.new_email, by_admin=outcome.by_admin,
                    app_url=base, site_timezone=tz,
                )
            else:
                await email_svc.send_email_change_alert(
                    to=outcome.old_email, locale=loc, display_name=name,
                    new_email=outcome.new_email, cancel_token=outcome.cancel_token,
                    by_admin=outcome.by_admin, applied=False,
                    app_url=base, site_timezone=tz,
                )
    except Exception:
        logger.exception(
            "email_change: request email dispatch failed (change already persisted)"
        )


async def dispatch_confirm_emails(db: Session, outcome: ConfirmOutcome) -> None:
    if not outcome.applied:
        return
    from . import email as email_svc
    from . import site as site_svc

    base = site_svc.get_site_url(db)
    tz = site_svc.get_site_timezone(db)
    try:
        if outcome.set_password_token:
            await email_svc.send_password_reset_email(
                to=outcome.new_email, locale=outcome.locale,
                display_name=outcome.display_name, token=outcome.set_password_token,
                app_url=base, site_timezone=tz,
            )
        await email_svc.send_email_change_completed(
            to=outcome.new_email, locale=outcome.locale,
            display_name=outcome.display_name, new_email=outcome.new_email,
            oidc_reset=outcome.oidc_reset, app_url=base, site_timezone=tz,
        )
    except Exception:
        logger.exception(
            "email_change: confirm email dispatch failed (change already persisted)"
        )
