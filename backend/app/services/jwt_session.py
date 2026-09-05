"""JWT access tokens + refresh-token rotation, eviction, reuse detection.

Separated from `services/auth.py` so the login-flow surface (password +
2FA + email-verify + password-reset) doesn't drag in the session-token
machinery and vice versa. `services/auth.py` imports
`create_access_token`, `create_refresh_token`, and
`revoke_all_user_refresh_tokens` from here on the happy-path login;
routers/auth.py imports `rotate_refresh` and `logout` directly.

Refresh-token rotation invariants the audit relies on:
- Refresh row is hash-only in the DB; plaintext only exists in the
  cookie set by the router.
- Conditional UPDATE in `rotate_refresh` is the atomic guard against
  double-use. The second concurrent request sees rowcount=0 and is
  SOFT-failed with INVALID_REFRESH - it is one client racing itself over a
  shared cookie, not theft. (This said "treats as reuse → family-revoke"
  until 376a851 changed it; the description outlived the behaviour.)
  Genuine reuse - replaying a link whose `replaced_by_id` is already set -
  is the branch that revokes the family.
- Per-user session cap is enforced via `enforce_session_cap` at every
  call site that mints a fresh refresh - oldest evicted first.
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

import jwt
from fastapi import Request
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.refresh_token import RefreshToken
from ..models.user import User
from ..utils.client_ip import get_client_ip
from ..utils.columns import declared_width
from ..utils.crypto import random_token, refresh_token_hash
from ..utils.dbresult import updated_rows
from ..utils.timeutil import to_epoch, utc_now, utc_now_aware
from .audit import record_audit_event

# Derived from the columns, not literals. `created_ua` was already clipped and
# `created_ip` on the very next line was not - an asymmetry inside one
# constructor call. The UA clip was the literal 255; deriving both means neither
# can drift from its column (027fe08's lesson: a clip to the wrong width is the
# same failure with a longer fuse).
_CREATED_IP_MAX = declared_width(RefreshToken.__table__.c.created_ip)
_CREATED_UA_MAX = declared_width(RefreshToken.__table__.c.created_ua)

if TYPE_CHECKING:
    from ..config import Settings

logger = logging.getLogger("fileheron.jwt_session")


# ---------------------------------------------------------------------------
# Access tokens (JWT)
# ---------------------------------------------------------------------------


def create_access_token(user_id: int, settings, db: Session | None = None) -> tuple[str, int]:
    """Returns (token, expires_in_seconds).

    Uses AWARE UTC for timestamp math - naive .timestamp() is interpreted as
    local time and would emit incorrect Unix epochs.
    Adds a `jti` (random nonce) so two tokens issued in the same second
    are still distinguishable.

    `db` is optional: when supplied, the access-token TTL is read live from
    the admin-tunable settings registry (kv overlay, env default); without
    it the env value is used (keeps non-DB call sites working).
    """
    if db is not None:
        from . import settings_registry
        minutes = settings_registry.effective(db, settings_registry.K.ACCESS_TOKEN_EXPIRE_MINUTES)
    else:
        minutes = settings.ACCESS_TOKEN_EXPIRE_MINUTES
    now_aware = utc_now_aware()
    exp_aware = now_aware + timedelta(minutes=minutes)
    payload = {
        "sub": str(user_id),
        "iat": int(now_aware.timestamp()),
        "exp": int(exp_aware.timestamp()),
        "jti": uuid.uuid4().hex,
        "type": "access",
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, minutes * 60


# A second factor cannot be demanded without somewhere to hold "first factor
# done, second factor outstanding". There was no such state: create_access_token
# minted exactly one token type, meaning "fully authenticated", with no amr/acr
# claim, so no downstream dependency could tell a one-factor session from a
# two-factor one. This is that state, kept deliberately additive - nothing about
# the access token changes, and resolve_user_from_access_token already rejects
# any type that is not "access", so a pending token fails closed everywhere a
# real one is expected.
PENDING_2FA_TTL_SEC = 300


def create_pending_2fa_token(user_id: int, settings, *, via: str) -> str:
    """Mint a short-lived credential proving only the FIRST factor.

    Five minutes: long enough to open an authenticator app, short enough that a
    token leaked via the redirect URL (referrer, history, shoulder) is stale
    before it is useful. It grants nothing on its own - the only endpoint that
    accepts it is the second-factor exchange.

    `via` records which first factor was presented, so the audit trail on the
    completed login still distinguishes an SSO login from a passkey one.
    """
    now_aware = utc_now_aware()
    payload = {
        "sub": str(user_id),
        "iat": int(now_aware.timestamp()),
        "exp": int((now_aware + timedelta(seconds=PENDING_2FA_TTL_SEC)).timestamp()),
        "jti": uuid.uuid4().hex,
        "type": "pending_2fa",
        "via": via,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def resolve_pending_2fa_token(db: Session, token: str, settings: Settings) -> tuple[User, str]:
    """Validate a pending-2FA token. Returns (user, via)."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise AppError(401, "PENDING_2FA_EXPIRED", "Sign-in timed out; start again.") from None
    except jwt.InvalidTokenError:
        raise AppError(401, "INVALID_TOKEN", "Invalid token.") from None

    if payload.get("type") != "pending_2fa":
        raise AppError(401, "INVALID_TOKEN", "Wrong token type.")
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError, TypeError):
        raise AppError(401, "INVALID_TOKEN", "Invalid token claims.") from None

    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None or user.is_disabled:
        raise AppError(401, "AUTH_REQUIRED", "Authentication failed.")

    iat = payload.get("iat")
    if isinstance(iat, int) and was_issued_before_revocation(user, iat):
        raise AppError(401, "SESSION_REVOKED", "This session was revoked.")

    return user, str(payload.get("via") or "unknown")


def resolve_user_from_access_token(db: Session, token: str, settings: Settings) -> User:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise AppError(401, "TOKEN_EXPIRED", "Access token has expired.") from None
    except jwt.InvalidTokenError:
        raise AppError(401, "INVALID_TOKEN", "Invalid access token.") from None

    if payload.get("type") != "access":
        raise AppError(401, "INVALID_TOKEN", "Wrong token type.")
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError, TypeError):
        raise AppError(401, "INVALID_TOKEN", "Invalid token claims.") from None

    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None or user.is_disabled:
        raise AppError(401, "AUTH_REQUIRED", "Authentication failed.")

    # Refuse a token minted before the user's sessions were invalidated. Same
    # row, no extra query.
    #
    # Compared at SECOND granularity, and deliberately with `<` rather than
    # `<=`: `iat` is whole seconds, and `routers/account.py::change_password`
    # revokes and then re-mints inside one request, so a strict comparison
    # against a sub-second mark would reject the very token it just issued and
    # sign the user out for changing their password. The cost is that a token
    # minted in the same second as the revoke survives - a <=1s window, against
    # the 15 minutes (up to 24 h on a raised TTL) it survived before.
    iat = payload.get("iat")
    if isinstance(iat, int) and was_issued_before_revocation(user, iat):
        raise AppError(401, "SESSION_REVOKED", "This session was revoked.")
    return user


def was_issued_before_revocation(user: User, issued_at_epoch: int) -> bool:
    """Whether a credential minted at `issued_at_epoch` predates the user's
    revocation mark.

    Shared with the SSE stream token, which is a SECOND bearer credential for
    the same session and was not consulting the mark at all - so a revoked
    session kept reading the event stream for the token's remaining life. Any
    future signed token standing in for a session has to come through here too.
    """
    invalidated = user.sessions_invalidated_at
    if invalidated is None:
        return False
    return issued_at_epoch < int(to_epoch(invalidated))


# ---------------------------------------------------------------------------
# Refresh tokens (DB-backed, rotated, reuse-detected)
# ---------------------------------------------------------------------------


def enforce_session_cap(
    db: Session, *, user_id: int, cap: int, request: Request | None
) -> int:
    """Revoke the oldest excess active tokens so that creating one
    more leaves the user at exactly `cap`. Returns number revoked.
    Called once per `create_refresh_token` from any auth flow."""
    if cap <= 0:
        return 0
    now = utc_now()
    active_q = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
    )
    active_count = active_q.count()
    if active_count < cap:
        return 0
    # Need to evict (active_count - cap + 1) to make room for the new one.
    excess = active_count - cap + 1
    oldest = (
        active_q.order_by(RefreshToken.created_at.asc(), RefreshToken.id.asc()).limit(excess).all()
    )
    for token in oldest:
        token.revoked_at = now
        record_audit_event(
            db,
            event_type=AuditEventType.refresh_token_evicted,
            actor_user_id=user_id,
            target_type="refresh_token",
            target_id=str(token.id),
            metadata={
                "evicted_token_id": token.id,
                "reason": "session_cap",
                "cap": cap,
            },
            request=request,
        )
    db.flush()
    if oldest:
        # Make the eviction visible instead of a silent sign-out. Best-effort:
        # dispatch failures never break the login that triggered the cap.
        try:
            from ..models.notification import NotificationCategory
            from . import notification as notif_svc

            evicted_user = db.query(User).filter(User.id == user_id).one_or_none()
            if evicted_user is not None:
                notif_svc.dispatch(
                    db,
                    user=evicted_user,
                    category=NotificationCategory.session_evicted,
                    payload={"count": len(oldest), "cap": cap},
                    link_url="/account",
                    email_to=evicted_user.email,
                )
        except Exception:
            logger.exception("session_evicted dispatch failed user=%d", user_id)
    return len(oldest)


def reclamp_refresh_expiry(
    db: Session, *, new_days: int, actor: User | None = None, request: Request | None = None
) -> dict:
    """Apply a shortened refresh-token TTL to EXISTING active sessions.

    Max-idle semantics: clamp ``expires_at`` down to ``last_activity + new_days``
    (``last_used_at``, or ``created_at`` for a never-rotated token) - never extend;
    a session idle longer than the new window is revoked. A session that keeps
    refreshing stays alive because each rotation re-anchors ``last_used_at``.
    Returns ``{clamped, revoked}``. Caller commits."""
    now = utc_now()
    rows = (
        db.query(RefreshToken)
        .filter(RefreshToken.revoked_at.is_(None), RefreshToken.expires_at > now)
        .all()
    )
    clamped = 0
    revoked = 0
    for t in rows:
        new_exp = (t.last_used_at or t.created_at) + timedelta(days=new_days)
        if new_exp < t.expires_at:
            t.expires_at = new_exp
            clamped += 1
        if t.expires_at <= now:
            t.revoked_at = now
            revoked += 1
    if clamped or revoked:
        db.flush()
        record_audit_event(
            db,
            event_type=AuditEventType.refresh_token_evicted,
            actor_user_id=actor.id if actor else None,
            target_type="refresh_token",
            target_id=None,
            metadata={
                "reason": "ttl_shortened",
                "new_days": new_days,
                "clamped": clamped,
                "revoked": revoked,
            },
            request=request,
        )
    return {"clamped": clamped, "revoked": revoked}


def create_refresh_token(db: Session, user: User, request: Request | None, settings) -> tuple[RefreshToken, str]:
    # Cap-enforcement chokepoint - every auth flow (password, recovery,
    # OIDC, WebAuthn, register-from-invite) ends here, so this gate
    # covers them all. The eviction is non-security-relevant
    # (`refresh_token_evicted` audit) - distinct from
    # `refresh_token_reused` family-revoke for compromised chains.
    from . import settings_registry
    enforce_session_cap(
        db,
        user_id=user.id,
        cap=settings_registry.effective(db, settings_registry.K.MAX_ACTIVE_SESSIONS_PER_USER),
        request=request,
    )

    # 64 raw bytes, base64url-encoded to 86 characters. The call asked for 48
    # while this comment, the RefreshToken model docstring and CLAUDE.md all
    # said 64; three documents asserting a number the code contradicts is how a
    # later change "restores" the wrong constant or reports the wrong entropy
    # budget, so make the code match them rather than the other way round.
    plaintext = random_token(64)
    now = utc_now()
    refresh_days = settings_registry.effective(
        db, settings_registry.K.REFRESH_TOKEN_EXPIRE_DAYS
    )
    record = RefreshToken(
        user_id=user.id,
        token_hash=refresh_token_hash(plaintext),
        expires_at=now + timedelta(days=refresh_days),
        # Canonical form (mapped IPv6 unwrapped) like login_attempts.ip, so the
        # session list and the login forensics show one address for one host.
        created_ip=(
            (get_client_ip(request) or "")[:_CREATED_IP_MAX] or None
            if request
            else None
        ),
        created_ua=(
            request.headers.get("user-agent", "")[:_CREATED_UA_MAX]
            if request
            else None
        ),
    )
    db.add(record)
    db.flush()
    return record, plaintext


def revoke_all_user_refresh_tokens(db: Session, user_id: int) -> int:
    """Coarse-but-safe family revoke. Used when reuse is detected or on
    password reset / change. Returns number of rows affected.

    Also stamps `users.sessions_invalidated_at`, which is what makes already-
    issued ACCESS tokens stop working. Revoking only the refresh rows left a
    stolen access JWT usable for its full TTL through password reset, password
    change, logout-others, admin revoke-all and config-backup import - every
    path that funnels through here - so "all sessions were revoked" was not true
    of the credential actually presented on each request.

    NB: this docstring listed `logout-others` for two releases while
    `POST /api/auth/sessions/revoke-others` did NOT call this function - it
    stamped the refresh rows itself. Fixed 2026-08-15; the route now calls this
    and re-mints the caller, since the mark is per-user.

    Deliberately NOT done by single-session `logout`: the mark is per-user, so
    bumping it there would sign the user out of every other tab because they
    closed one. That session's own access token dies with the tab and is capped
    by the TTL regardless.
    """
    now = utc_now()
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.execute(update(User).where(User.id == user_id).values(sessions_invalidated_at=now))
    return updated_rows(result) or 0


def rotate_refresh(
    db: Session,
    *,
    refresh_token_plain: str,
    request: Request | None,
    settings,
) -> tuple[User, str, int, str]:
    """Validate + rotate the refresh token. On reuse → revoke all of the
    user's refresh tokens and raise TOKEN_REUSE.

    Returns (user, new_access_token, expires_in_seconds, new_refresh_token_plain).
    """
    record = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == refresh_token_hash(refresh_token_plain))
        .one_or_none()
    )
    if record is None:
        raise AppError(401, "INVALID_REFRESH", "Invalid refresh token.")
    if record.expires_at < utc_now():
        raise AppError(401, "INVALID_REFRESH", "Refresh token expired.")
    if record.revoked_at is not None:
        if record.replaced_by_id is None:
            # Deliberately revoked (logout-others, session-cap eviction, password
            # change/reset, email change, admin revoke, config restore) - NOT a
            # rotation-chain replay. Fail this refresh softly; don't nuke the whole
            # family or raise a false theft alarm.
            raise AppError(401, "INVALID_REFRESH", "Refresh token revoked.")
        # A token that was already ROTATED (replaced_by_id set) is being replayed →
        # genuine reuse of a stale chain link. Kill all sessions, audit, raise.
        revoke_all_user_refresh_tokens(db, record.user_id)
        record_audit_event(
            db,
            event_type=AuditEventType.refresh_token_reused,
            actor_user_id=record.user_id,
            target_type="refresh_token",
            target_id=record.id,
            request=request,
        )
        db.commit()
        raise AppError(401, "TOKEN_REUSE", "Refresh token reuse detected; all sessions revoked.")

    # Conditional UPDATE → atomic check-and-revoke. If two requests race, the
    # second sees affected_rows=0; see the branch below for what happens then -
    # it is NOT treated as reuse, which is what this comment used to claim.
    now = utc_now()
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == record.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    if (updated_rows(result) or 0) == 0:
        # The token was valid when we read it but got revoked between the read and
        # this UPDATE - a concurrent legitimate operation (two browser tabs sharing
        # the cookie both rotating, or a deliberate revoke firing at the same
        # instant), NOT theft. Genuine reuse - replaying an already-rotated token -
        # is caught by the replaced_by_id branch above on the next attempt. Fail
        # this racer softly instead of nuking the family.
        raise AppError(401, "INVALID_REFRESH", "Refresh token already rotated.")

    user = db.query(User).filter(User.id == record.user_id).one_or_none()
    if user is None or user.is_disabled:
        raise AppError(403, "ACCOUNT_DISABLED", "Account unavailable.")

    new_record, new_plain = create_refresh_token(db, user, request, settings)
    # Carry the original sign-in time forward across the rotation chain so
    # `created_at` keeps meaning "session start"; `last_used_at` tracks this
    # latest activity (admins sort by it to spot stale/hanging sessions).
    new_record.created_at = record.created_at
    new_record.last_used_at = now
    record.replaced_by_id = new_record.id
    db.flush()

    access, expires_in = create_access_token(user.id, settings, db)
    record_audit_event(
        db,
        event_type=AuditEventType.refresh_token_rotated,
        actor_user_id=user.id,
        target_type="refresh_token",
        target_id=new_record.id,
        request=request,
    )
    return user, access, expires_in, new_plain


def logout(db: Session, *, refresh_token_plain: str, request: Request | None) -> None:
    record = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == refresh_token_hash(refresh_token_plain))
        .one_or_none()
    )
    if record is None or record.revoked_at is not None:
        return  # idempotent
    record.revoked_at = utc_now()
    record_audit_event(
        db,
        event_type=AuditEventType.logout,
        actor_user_id=record.user_id,
        target_type="refresh_token",
        target_id=record.id,
        request=request,
    )
