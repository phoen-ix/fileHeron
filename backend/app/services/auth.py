"""Authentication service. The core of Phase 1a.

Includes:
- register_from_invite: consumes invite + creates user (email already verified)
- login: password-only in Phase 1a (TOTP added in Phase 1b)
- rotate_refresh: refresh-token rotation with reuse-detection (revokes all of
  the user's refresh tokens on detected reuse)
- logout: revoke single refresh
- forgot_password / reset_password: 1h single-use token; reset revokes all sessions
- verify_email / resend_verification
- change_password: verifies current, HIBP-checks new, revokes all sessions
- resolve_user_from_access_token: JWT decoder used by `dependencies.get_current_user`
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import jwt
from fastapi import Request
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.email_verify_token import EmailVerifyToken
from ..models.invite_token import InviteToken
from ..models.known_device import KnownDevice
from ..models.login_attempt import LoginAttempt, LoginOutcome
from ..models.password_reset_token import PasswordResetToken
from ..models.refresh_token import RefreshToken
from ..models.user import Locale, User, UserRole
from ..utils.crypto import (
    argon2_hash,
    argon2_verify,
    normalize_email,
    random_token,
    refresh_token_hash,
    sha256_hex,
)
from ..utils.geohash import ip_geohash5
from ..utils.ua_fingerprint import ua_fingerprint_hash
from . import rate_limit as rate_limit_svc
from . import totp as totp_svc
from .audit import record_audit_event
from .hibp import is_password_breached

if TYPE_CHECKING:
    from ..config import Settings


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INVITE_TTL = timedelta(hours=24)
EMAIL_VERIFY_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(hours=1)


def _utcnow() -> datetime:
    # Naive UTC — matches what MariaDB returns for DATETIME columns.
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Access tokens (JWT)
# ---------------------------------------------------------------------------


def create_access_token(user_id: int, settings) -> tuple[str, int]:
    """Returns (token, expires_in_seconds).

    Uses AWARE UTC for timestamp math — naive .timestamp() is interpreted as
    local time and would emit incorrect Unix epochs.
    Adds a `jti` (random nonce) so two tokens issued in the same second
    are still distinguishable.
    """
    now_aware = datetime.now(tz=timezone.utc)
    exp_aware = now_aware + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "iat": int(now_aware.timestamp()),
        "exp": int(exp_aware.timestamp()),
        "jti": uuid.uuid4().hex,
        "type": "access",
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60


def resolve_user_from_access_token(db: Session, token: str, settings: "Settings") -> User:
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
    return user


# ---------------------------------------------------------------------------
# Refresh tokens (DB-backed, rotated, reuse-detected)
# ---------------------------------------------------------------------------


def _enforce_session_cap(
    db: Session, *, user_id: int, cap: int, request: Request | None
) -> int:
    """Revoke the oldest excess active tokens so that creating one
    more leaves the user at exactly `cap`. Returns number revoked.
    Called once per `_create_refresh_token` from any auth flow."""
    if cap <= 0:
        return 0
    now = _utcnow()
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
        active_q.order_by(RefreshToken.created_at.asc()).limit(excess).all()
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
    return len(oldest)


def _create_refresh_token(db: Session, user: User, request: Request | None, settings) -> tuple[RefreshToken, str]:
    # Cap-enforcement chokepoint — every auth flow (password, recovery,
    # OIDC, WebAuthn, register-from-invite) ends here, so this gate
    # covers them all. The eviction is non-security-relevant
    # (`refresh_token_evicted` audit) — distinct from
    # `refresh_token_reused` family-revoke for compromised chains.
    _enforce_session_cap(
        db,
        user_id=user.id,
        cap=settings.MAX_ACTIVE_SESSIONS_PER_USER,
        request=request,
    )

    plaintext = random_token(48)  # 64 raw bytes → 86-char b64url
    now = _utcnow()
    record = RefreshToken(
        user_id=user.id,
        token_hash=refresh_token_hash(plaintext),
        expires_at=now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        created_ip=(request.client.host if request and request.client else None),
        created_ua=(request.headers.get("user-agent", "")[:255] if request else None),
    )
    db.add(record)
    db.flush()
    return record, plaintext


def _revoke_all_user_refresh_tokens(db: Session, user_id: int) -> int:
    """Coarse-but-safe family revoke. Used when reuse is detected or on
    password reset / change. Returns number of rows affected."""
    now = _utcnow()
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# Public auth flows
# ---------------------------------------------------------------------------


def register_from_invite(
    db: Session,
    *,
    plaintext_token: str,
    password: str,
    display_name: str,
    locale: Locale,
    request: Request | None,
) -> User:
    """Consume an invite, create the corresponding user (email pre-verified).

    Caller is responsible for committing.
    """
    invite = (
        db.query(InviteToken).filter(InviteToken.token_hash == sha256_hex(plaintext_token)).one_or_none()
    )
    if invite is None:
        raise AppError(404, "INVITE_INVALID", "Invite is invalid.")
    if invite.used_at is not None:
        raise AppError(410, "INVITE_USED", "Invite has already been used.")
    if invite.expires_at < _utcnow():
        raise AppError(410, "INVITE_EXPIRED", "Invite has expired.")

    # Email is implied by the invite — we never get the plaintext here, so we
    # match by the stored hash. If a user already exists with that hash, we
    # reject (they already have an account).
    existing = db.query(User).filter(User.email == invite.email).one_or_none()
    if existing is not None:
        raise AppError(409, "USER_EXISTS", "An account already exists for this email.")

    user = User(
        email=invite.email,
        password_hash=argon2_hash(password),
        display_name=display_name,
        role=invite.target_role,
        locale=locale,
        email_verified=True,
        is_disabled=False,
        created_by_id=invite.created_by_id,
    )
    db.add(user)
    db.flush()

    invite.used_at = _utcnow()
    invite.used_user_id = user.id

    # Apply pre-assigned group memberships, if any. A group deleted between
    # invite creation and consume is silently skipped (defensive).
    inviter = db.query(User).filter(User.id == invite.created_by_id).one_or_none()
    if invite.initial_group_ids:
        from ..models.group import Group
        from .group import add_member as _add_group_member

        actor = inviter or user  # fall back to self if inviter was deleted
        for gid in invite.initial_group_ids:
            grp = db.query(Group).filter(Group.id == gid).one_or_none()
            if grp is None:
                continue
            _add_group_member(db, actor=actor, group=grp, user=user, request=request)

    # If the inviter was an employee/admin and the invitee turns out to be a
    # client (or vice versa), record the sticky `invite` connection. Done
    # via the connection service so the rule lives in one place.
    if inviter is not None:
        from .connection import record_invite_connection
        record_invite_connection(db, inviter=inviter, invitee=user)

        # Phase 6a: notify the inviter that their invite was consumed.
        from ..models.notification import NotificationCategory
        from . import notification as notif_svc
        from . import site as site_svc

        account_url = f"{site_svc.get_site_url(db)}/account"
        notif_svc.dispatch(
            db,
            user=inviter,
            category=NotificationCategory.account_created,
            payload={
                "inviter_name": inviter.display_name,
                "invitee_name": user.display_name,
                "invitee_role": user.role.value,
                "account_url": account_url,
            },
            link_url=account_url,
            email_to=inviter.email,
        )

    record_audit_event(
        db,
        event_type=AuditEventType.user_registered,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.invite_consumed,
        actor_user_id=user.id,
        target_type="invite",
        target_id=invite.id,
        request=request,
    )
    return user


def _record_login_attempt(
    db: Session,
    *,
    email_value: str | None,
    ip: str | None,
    outcome: LoginOutcome,
) -> None:
    db.add(LoginAttempt(email=email_value, ip=ip, outcome=outcome.value))
    db.flush()


def _request_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    return request.client.host


def _record_login_device(db: Session, *, user: User, request: Request | None) -> bool:
    """Upsert KnownDevice for (user, UA fingerprint, IP geohash). Returns
    True if this is the first time we've seen this device tuple (Phase 7 will
    fire a new-device alert email then)."""
    if request is None:
        return False
    ip = _request_ip(request) or ""
    ua = request.headers.get("user-agent", "")
    geo = ip_geohash5(ip)
    ua_hash = ua_fingerprint_hash(ua)
    if not geo or not ua_hash:
        return False

    existing = (
        db.query(KnownDevice)
        .filter(
            KnownDevice.user_id == user.id,
            KnownDevice.ua_fingerprint_hash == ua_hash,
            KnownDevice.ip_geohash == geo,
        )
        .one_or_none()
    )
    now = _utcnow()
    if existing is not None:
        existing.last_seen = now
        db.flush()
        return False
    db.add(KnownDevice(user_id=user.id, ua_fingerprint_hash=ua_hash, ip_geohash=geo))
    db.flush()
    return True


async def _maybe_send_lockout_email(
    *,
    db: Session,
    user: User,
    email_plaintext: str,
    request: Request | None,
) -> None:
    """Phase 1b lockout warning email. Imported lazily to avoid a circular
    import (services.email → models.user → services.auth)."""
    from .email import send_lockout_warning_email
    from . import site as site_svc

    locked_until = user.locked_until.isoformat() if user.locked_until else "soon"
    ip = _request_ip(request)
    ip_hint = f"~{ip_geohash5(ip)}" if ip else None
    await send_lockout_warning_email(
        to=email_plaintext,
        locale=user.locale,
        display_name=user.display_name,
        locked_until_iso=locked_until,
        ip_hint=ip_hint,
        app_url=site_svc.get_site_url(db),
    )


async def login(
    db: Session,
    *,
    email: str,
    password: str,
    totp_code: str | None = None,
    request: Request | None,
    settings,
) -> tuple[User, str, int, str]:
    """Phase 1b: full auth flow with TOTP + lockout + device recording.

    Returns (user, access_token, expires_in_seconds, refresh_token_plain) on
    success. Raises AppError otherwise. Possible error codes:
    - RATE_LIMITED      — per-IP login rate exceeded
    - INVALID_CREDENTIALS — bad email or bad password
    - ACCOUNT_LOCKED    — account-level lockout active (15min)
    - ACCOUNT_DISABLED
    - EMAIL_NOT_VERIFIED
    - TOTP_REQUIRED     — 2FA is on, totp_code missing
    - INVALID_TOTP      — totp_code wrong (also bumps failure counter)
    """
    ip = _request_ip(request)

    # 1. Per-IP rate limit
    if ip and not rate_limit_svc.check_login_ip_allowed(ip):
        _record_login_attempt(db, email_value=None, ip=ip, outcome=LoginOutcome.rate_limited)
        db.commit()
        raise AppError(429, "RATE_LIMITED", "Too many login attempts. Try again later.")

    em_email = normalize_email(email)
    user = db.query(User).filter(User.email == em_email).one_or_none()

    # 2. Unknown email → bad credentials (no oracle: same response either way)
    if user is None:
        _record_login_attempt(db, email_value=em_email, ip=ip, outcome=LoginOutcome.unknown_email)
        record_audit_event(
            db,
            event_type=AuditEventType.login_failure,
            actor_user_id=None,
            target_type="user",
            target_id=None,
            metadata={"reason": "unknown_email"},
            request=request,
        )
        db.commit()
        raise AppError(401, "INVALID_CREDENTIALS", "Invalid email or password.")

    # 3. Account lockout (return 423 with retry-after hint baked in details)
    if rate_limit_svc.is_account_locked(user):
        _record_login_attempt(db, email_value=em_email, ip=ip, outcome=LoginOutcome.locked)
        record_audit_event(
            db,
            event_type=AuditEventType.account_locked,
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            metadata={"locked_until": user.locked_until.isoformat() if user.locked_until else None},
            request=request,
        )
        db.commit()
        raise AppError(
            423,
            "ACCOUNT_LOCKED",
            "Account is temporarily locked due to too many failed attempts.",
            details={"locked_until": user.locked_until.isoformat() if user.locked_until else None},
        )

    # 4. Verify password
    if not argon2_verify(user.password_hash, password):
        just_locked, should_email = rate_limit_svc.record_failure(db, user=user)
        _record_login_attempt(db, email_value=em_email, ip=ip, outcome=LoginOutcome.bad_password)
        record_audit_event(
            db,
            event_type=AuditEventType.login_failure,
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            metadata={"reason": "bad_password", "just_locked": just_locked},
            request=request,
        )
        if just_locked:
            record_audit_event(
                db,
                event_type=AuditEventType.account_locked,
                actor_user_id=user.id,
                target_type="user",
                target_id=user.id,
                request=request,
            )
        if should_email:
            try:
                await _maybe_send_lockout_email(
                    db=db, user=user, email_plaintext=email, request=request
                )
                rate_limit_svc.mark_lockout_email_sent(db, user=user)
            except Exception:
                # Logging will catch this; lockout still applies.
                pass
        db.commit()
        raise AppError(401, "INVALID_CREDENTIALS", "Invalid email or password.")

    # 5. Disabled / not-verified
    if user.is_disabled:
        _record_login_attempt(db, email_value=em_email, ip=ip, outcome=LoginOutcome.account_disabled)
        record_audit_event(
            db,
            event_type=AuditEventType.login_failure,
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            metadata={"reason": "account_disabled"},
            request=request,
        )
        db.commit()
        raise AppError(403, "ACCOUNT_DISABLED", "This account has been disabled.")

    if not user.email_verified:
        _record_login_attempt(db, email_value=em_email, ip=ip, outcome=LoginOutcome.email_not_verified)
        db.commit()
        raise AppError(403, "EMAIL_NOT_VERIFIED", "Please verify your email first.")

    # 6. TOTP challenge (if enabled)
    if totp_svc.is_enabled(user):
        if not totp_code:
            db.commit()
            raise AppError(401, "TOTP_REQUIRED", "Two-factor code required.")
        if not totp_svc.verify_at_login(db, user=user, code=totp_code):
            just_locked, should_email = rate_limit_svc.record_failure(db, user=user)
            _record_login_attempt(db, email_value=em_email, ip=ip, outcome=LoginOutcome.bad_totp)
            record_audit_event(
                db,
                event_type=AuditEventType.login_failure,
                actor_user_id=user.id,
                target_type="user",
                target_id=user.id,
                metadata={"reason": "bad_totp", "just_locked": just_locked},
                request=request,
            )
            if just_locked:
                record_audit_event(
                    db,
                    event_type=AuditEventType.account_locked,
                    actor_user_id=user.id,
                    target_type="user",
                    target_id=user.id,
                    request=request,
                )
            if should_email:
                try:
                    await _maybe_send_lockout_email(
                        user=user, email_plaintext=email, request=request
                    )
                    rate_limit_svc.mark_lockout_email_sent(db, user=user)
                except Exception:
                    pass
            db.commit()
            raise AppError(401, "INVALID_TOTP", "Two-factor code is invalid.")

    # 7. Success path
    rate_limit_svc.record_success(db, user=user)
    rate_limit_svc.reset_ip_window(ip or "")

    access, expires_in = create_access_token(user.id, settings)
    _, refresh_plain = _create_refresh_token(db, user, request, settings)

    is_new_device = _record_login_device(db, user=user, request=request)
    _record_login_attempt(db, email_value=em_email, ip=ip, outcome=LoginOutcome.success)
    record_audit_event(
        db,
        event_type=AuditEventType.login_success,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        metadata={"new_device": is_new_device, "via": "password"},
        request=request,
    )
    if is_new_device:
        from .login_alert import fire_new_device_alert
        fire_new_device_alert(db, user=user, request=request, via="password")

    return user, access, expires_in, refresh_plain


async def login_with_recovery(
    db: Session,
    *,
    email: str,
    password: str,
    recovery_code: str,
    request: Request | None,
    settings,
) -> tuple[User, str, int, str]:
    """Login using a recovery code instead of a TOTP code. Same auth gates as
    `login()` (rate limit, lockout, password, disabled, email-verified). On
    success, the recovery code is consumed (cannot be re-used)."""
    ip = _request_ip(request)

    if ip and not rate_limit_svc.check_login_ip_allowed(ip):
        _record_login_attempt(db, email_value=None, ip=ip, outcome=LoginOutcome.rate_limited)
        db.commit()
        raise AppError(429, "RATE_LIMITED", "Too many login attempts. Try again later.")

    em_email = normalize_email(email)
    user = db.query(User).filter(User.email == em_email).one_or_none()
    if user is None:
        _record_login_attempt(db, email_value=em_email, ip=ip, outcome=LoginOutcome.unknown_email)
        db.commit()
        raise AppError(401, "INVALID_CREDENTIALS", "Invalid email or password.")
    if rate_limit_svc.is_account_locked(user):
        _record_login_attempt(db, email_value=em_email, ip=ip, outcome=LoginOutcome.locked)
        db.commit()
        raise AppError(423, "ACCOUNT_LOCKED", "Account is temporarily locked.")
    if not argon2_verify(user.password_hash, password):
        rate_limit_svc.record_failure(db, user=user)
        _record_login_attempt(db, email_value=em_email, ip=ip, outcome=LoginOutcome.bad_password)
        db.commit()
        raise AppError(401, "INVALID_CREDENTIALS", "Invalid email or password.")
    if user.is_disabled:
        db.commit()
        raise AppError(403, "ACCOUNT_DISABLED", "This account has been disabled.")
    if not user.email_verified:
        db.commit()
        raise AppError(403, "EMAIL_NOT_VERIFIED", "Please verify your email first.")
    if not totp_svc.is_enabled(user):
        # No 2FA configured → recovery codes don't apply. Use /login.
        db.commit()
        raise AppError(409, "TOTP_NOT_ENABLED", "Two-factor auth is not enabled for this account.")

    if not totp_svc.consume_recovery_code(db, user=user, code=recovery_code, request=request):
        rate_limit_svc.record_failure(db, user=user)
        _record_login_attempt(db, email_value=em_email, ip=ip, outcome=LoginOutcome.bad_recovery)
        record_audit_event(
            db,
            event_type=AuditEventType.login_failure,
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            metadata={"reason": "bad_recovery"},
            request=request,
        )
        db.commit()
        raise AppError(401, "INVALID_RECOVERY", "Recovery code is invalid or already used.")

    rate_limit_svc.record_success(db, user=user)
    rate_limit_svc.reset_ip_window(ip or "")

    access, expires_in = create_access_token(user.id, settings)
    _, refresh_plain = _create_refresh_token(db, user, request, settings)
    is_new_device = _record_login_device(db, user=user, request=request)
    _record_login_attempt(db, email_value=em_email, ip=ip, outcome=LoginOutcome.success)
    record_audit_event(
        db,
        event_type=AuditEventType.login_success,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        metadata={"new_device": is_new_device, "via": "recovery_code"},
        request=request,
    )
    if is_new_device:
        from .login_alert import fire_new_device_alert
        fire_new_device_alert(db, user=user, request=request, via="recovery_code")
    return user, access, expires_in, refresh_plain


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
    if record.expires_at < _utcnow():
        raise AppError(401, "INVALID_REFRESH", "Refresh token expired.")
    if record.revoked_at is not None:
        # Reuse detected → kill all sessions for this user, audit, raise.
        _revoke_all_user_refresh_tokens(db, record.user_id)
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
    # second sees affected_rows=0 and we treat it as reuse.
    now = _utcnow()
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == record.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    if (result.rowcount or 0) == 0:
        _revoke_all_user_refresh_tokens(db, record.user_id)
        record_audit_event(
            db,
            event_type=AuditEventType.refresh_token_reused,
            actor_user_id=record.user_id,
            target_type="refresh_token",
            target_id=record.id,
            metadata={"reason": "race"},
            request=request,
        )
        db.commit()
        raise AppError(401, "TOKEN_REUSE", "Refresh token reuse detected; all sessions revoked.")

    user = db.query(User).filter(User.id == record.user_id).one_or_none()
    if user is None or user.is_disabled:
        raise AppError(403, "ACCOUNT_DISABLED", "Account unavailable.")

    new_record, new_plain = _create_refresh_token(db, user, request, settings)
    record.replaced_by_id = new_record.id
    db.flush()

    access, expires_in = create_access_token(user.id, settings)
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
    record.revoked_at = _utcnow()
    record_audit_event(
        db,
        event_type=AuditEventType.logout,
        actor_user_id=record.user_id,
        target_type="refresh_token",
        target_id=record.id,
        request=request,
    )


def begin_password_reset(
    db: Session, *, email: str, request: Request | None
) -> tuple[User, str] | None:
    """Returns (user, plaintext_token) or None if no user with that email
    exists. Caller awaits the email send. We never reveal account existence
    to the client — the API endpoint always returns 200 regardless.
    """
    em_email = normalize_email(email)
    user = db.query(User).filter(User.email == em_email).one_or_none()
    if user is None or user.is_disabled:
        return None

    plaintext = random_token(32)
    now = _utcnow()
    record = PasswordResetToken(
        user_id=user.id,
        token_hash=sha256_hex(plaintext),
        expires_at=now + PASSWORD_RESET_TTL,
    )
    db.add(record)
    record_audit_event(
        db,
        event_type=AuditEventType.password_reset_requested,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        request=request,
    )
    return user, plaintext


async def consume_password_reset(
    db: Session, *, plaintext_token: str, new_password: str, request: Request | None
) -> User:
    record = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token_hash == sha256_hex(plaintext_token))
        .one_or_none()
    )
    if record is None:
        raise AppError(404, "RESET_TOKEN_INVALID", "Reset link is invalid.")
    if record.used_at is not None:
        raise AppError(410, "RESET_TOKEN_USED", "Reset link has already been used.")
    if record.expires_at < _utcnow():
        raise AppError(410, "RESET_TOKEN_EXPIRED", "Reset link has expired.")

    if await is_password_breached(new_password):
        raise AppError(
            422, "PASSWORD_BREACHED", "Chosen password has appeared in a breach. Pick another."
        )

    user = db.query(User).filter(User.id == record.user_id).one()
    user.password_hash = argon2_hash(new_password)
    record.used_at = _utcnow()

    _revoke_all_user_refresh_tokens(db, user.id)

    record_audit_event(
        db,
        event_type=AuditEventType.password_reset_consumed,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        request=request,
    )
    return user


def begin_email_verification(db: Session, *, user: User) -> str:
    """Issue a fresh email-verify token for `user`. Returns plaintext.
    Caller is responsible for sending the email.
    """
    plaintext = random_token(32)
    now = _utcnow()
    record = EmailVerifyToken(
        user_id=user.id,
        token_hash=sha256_hex(plaintext),
        expires_at=now + EMAIL_VERIFY_TTL,
    )
    db.add(record)
    db.flush()
    return plaintext


def consume_email_verification(db: Session, *, plaintext_token: str, request: Request | None) -> User:
    record = (
        db.query(EmailVerifyToken)
        .filter(EmailVerifyToken.token_hash == sha256_hex(plaintext_token))
        .one_or_none()
    )
    if record is None:
        raise AppError(404, "VERIFY_TOKEN_INVALID", "Verification link is invalid.")
    if record.used_at is not None:
        raise AppError(410, "VERIFY_TOKEN_USED", "Verification link has already been used.")
    if record.expires_at < _utcnow():
        raise AppError(410, "VERIFY_TOKEN_EXPIRED", "Verification link has expired.")

    record.used_at = _utcnow()
    user = db.query(User).filter(User.id == record.user_id).one()
    user.email_verified = True

    record_audit_event(
        db,
        event_type=AuditEventType.email_verified,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        request=request,
    )
    return user


async def change_password(
    db: Session, *, user: User, current_password: str, new_password: str, request: Request | None
) -> None:
    if not argon2_verify(user.password_hash, current_password):
        raise AppError(401, "INVALID_CREDENTIALS", "Current password is incorrect.")
    if await is_password_breached(new_password):
        raise AppError(422, "PASSWORD_BREACHED", "Chosen password has appeared in a breach. Pick another.")

    user.password_hash = argon2_hash(new_password)
    _revoke_all_user_refresh_tokens(db, user.id)
    record_audit_event(
        db,
        event_type=AuditEventType.password_changed,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        request=request,
    )


# Convenience: re-export user role (kept simple — services have flat exports)
_ALL_ROLES = (UserRole.admin, UserRole.employee, UserRole.client)
