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

import asyncio
import logging
from datetime import timedelta

from fastapi import Request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.email_verify_token import EmailVerifyToken
from ..models.invite_token import InviteToken
from ..models.known_device import KnownDevice
from ..models.login_attempt import LoginAttempt, LoginOutcome
from ..models.password_reset_token import PasswordResetToken
from ..models.user import Locale, User, UserRole
from ..utils.client_ip import get_client_ip
from ..utils.columns import declared_width
from ..utils.crypto import (
    argon2_hash,
    argon2_verify,
    normalize_email,
    random_token,
    sha256_hex,
)
from ..utils.dbresult import updated_rows
from ..utils.geohash import ip_geohash5
from ..utils.timeutil import utc_now
from ..utils.ua_fingerprint import ua_fingerprint_hash
from . import jwt_session, settings_registry
from . import rate_limit as rate_limit_svc
from . import totp as totp_svc
from .audit import record_audit_event
from .hibp import assert_password_not_breached
from .jwt_session import (
    create_access_token,
    create_refresh_token,
    resolve_user_from_access_token,
    revoke_all_user_refresh_tokens,
)

# Derived from the columns rather than the literal 254 this used to carry. The
# comment at the write site explains why the EMAIL must be clipped; `ip` sat
# unclipped on the next line, and it is the same anonymous-caller-controlled
# value once an edge appends to X-Forwarded-For instead of overwriting it.
_ATTEMPT_EMAIL_MAX = declared_width(LoginAttempt.__table__.c.email)
_ATTEMPT_IP_MAX = declared_width(LoginAttempt.__table__.c.ip)

logger = logging.getLogger("fileheron.auth")

# Re-exported names for backwards-compatibility with callers that still
# import these from services.auth (dependencies.py uses
# resolve_user_from_access_token; tests likely use create_access_token).
__all__ = [
    "create_access_token",
    "resolve_user_from_access_token",
    "revoke_all_user_refresh_tokens",
    "create_refresh_token",
    "authenticate_first_factor",
    "login",
    "login_with_recovery",
    "register_from_invite",
    "begin_password_reset",
    "consume_password_reset",
    "begin_email_verification",
    "consume_email_verification",
    "change_password",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMAIL_VERIFY_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(hours=1)

# A fixed Argon2 hash used only to equalize wall-clock time on the
# unknown-email branch of authenticate_first_factor, so a missing account
# cannot be told apart from a wrong password by response latency. The
# plaintext below is never a usable credential.
_DUMMY_PASSWORD_HASH = argon2_hash("fileheron-login-timing-equalizer")


# ---------------------------------------------------------------------------
# Public auth flows
# ---------------------------------------------------------------------------


def _create_user_from_invite(
    db: Session,
    *,
    invite: InviteToken,
    password: str,
    display_name: str,
    locale: Locale,
    via: str,
    request: Request | None,
) -> User:
    """Shared post-validation creation path for invite consumption.

    Two callers:
    - `register_from_invite` (self-registration): caller validated the
      plaintext token and confirmed the invite is not used/expired.
    - `services/invite.py::activate_invite_as_admin`: an admin chose
      to bypass the invitee's password-set step. Admins may activate
      expired invites; only the `used_at` check is honoured by them.

    `via` discriminates the two in the `invite_consumed` audit
    extra (`"self_register"` vs `"admin_direct"`) - same pattern as
    `oidc_linked` extra.via.

    Raises ``AppError(409, USER_EXISTS)`` if a registered account
    already exists for this email (the invite is for an email that
    has since been claimed). Caller commits.
    """
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
    try:
        db.flush()
    except IntegrityError:
        # A parallel registration won the UNIQUE(email) race between the
        # preflight SELECT above and this flush - surface the clean 409 rather
        # than a raw 500 (mirrors services/email_change.py's apply path).
        raise AppError(409, "USER_EXISTS", "An account already exists for this email.") from None

    invite.used_at = utc_now()
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

        # Notify the inviter that their invite was consumed.
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
                "via": via,
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
        metadata={"via": via},
        request=request,
    )
    return user


async def register_from_invite(
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
    if invite.expires_at < utc_now():
        raise AppError(410, "INVITE_EXPIRED", "Invite has expired.")
    await assert_password_not_breached(db, password)
    return _create_user_from_invite(
        db,
        invite=invite,
        password=password,
        display_name=display_name,
        locale=locale,
        via="self_register",
        request=request,
    )


def _record_login_attempt(
    db: Session,
    *,
    email_value: str | None,
    ip: str | None,
    outcome: LoginOutcome,
) -> None:
    # Clip to the column width before inserting. This row is written on the
    # unknown-email branch too, so the value is whatever an anonymous caller
    # sent; under MariaDB strict mode an over-long one raises DataError, which
    # turns an intended 401 into a 500 that also writes an error_log row and can
    # page an admin - repeatable at will, without credentials. The schema cap on
    # EmailLike is the real guard; forensics must never be able to fail the
    # request it is recording.
    db.add(
        LoginAttempt(
            email=email_value[:_ATTEMPT_EMAIL_MAX] if email_value else None,
            ip=ip[:_ATTEMPT_IP_MAX] if ip else None,
            outcome=outcome.value,
        )
    )
    db.flush()


def _request_ip(request: Request | None) -> str | None:
    """The caller's address, in the SAME canonical form the rest of the product
    uses.

    Through `get_client_ip`, not `request.client.host` directly, so IPv4-mapped
    IPv6 is unwrapped here too. `login_attempts.ip` is not just a log: the scan
    guard's shared-egress check joins it against the address the middleware
    counted, which IS normalised. Leaving this raw meant that on a dual-stack
    deployment the join found zero rows, so the office whose successful logins
    should have exempted it got blocked instead - the exact false positive that
    check exists to prevent, failing silently.
    """
    if request is None:
        return None
    return get_client_ip(request)


def _record_login_device(db: Session, *, user: User, request: Request | None) -> bool:
    """Upsert KnownDevice for (user, UA fingerprint, IP geohash). Returns
    True if this is the first time we've seen this device tuple; the login
    handler then fires a new-device alert email via
    `services/login_alert.fire_new_device_alert`."""
    if request is None:
        return False
    ip = _request_ip(request) or ""
    # An ABSENT User-Agent is itself a device fingerprint, not a reason to skip
    # the check. ua_fingerprint_hash("") returns "", and bailing on a falsy hash
    # meant an attacker who simply omitted the header recorded no known_devices
    # row, fired no new-device alert, and stamped `new_device: False` into the
    # audit row - across every login path, since they all funnel through
    # finalize_successful_login (audit 2026-07-30). Fingerprint the absence.
    ua = request.headers.get("user-agent", "") or "-"
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
    now = utc_now()
    if existing is not None:
        existing.last_seen = now
        db.flush()
        return False
    db.add(KnownDevice(user_id=user.id, ua_fingerprint_hash=ua_hash, ip_geohash=geo))
    db.flush()
    return True


def finalize_successful_login(
    db: Session,
    *,
    user: User,
    request: Request | None,
    settings,
    via: str,
    email_value: str | None = None,
    notify_new_device: bool = True,
) -> tuple[str, int, str]:
    """Mint the session and record the forensic trail shared by EVERY
    successful-login flow: access + refresh tokens, known-device upsert,
    login_attempts row, login_success audit, and the new-device alert. `via` tags
    the flow (password / recovery_code / oidc / webauthn). Returns
    ``(access_token, expires_in_seconds, refresh_token_plain)``. Caller commits.

    `notify_new_device=False` seeds the device row without the alert. Used by
    registration-from-invite: consuming the invite is itself proof of control,
    and the device is new by definition, so alerting says "we noticed a login
    from a new device" about the account's very first second - training the
    reader to ignore the one message that matters later."""
    access, expires_in = create_access_token(user.id, settings, db)
    _, refresh_plain = create_refresh_token(db, user, request, settings)
    is_new_device = _record_login_device(db, user=user, request=request)
    _record_login_attempt(
        db, email_value=email_value or user.email, ip=_request_ip(request),
        outcome=LoginOutcome.success,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.login_success,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        metadata={"new_device": is_new_device, "via": via},
        request=request,
    )
    if is_new_device and notify_new_device:
        from .login_alert import fire_new_device_alert
        fire_new_device_alert(db, user=user, request=request, via=via)
    return access, expires_in, refresh_plain


async def _maybe_send_lockout_email(
    *,
    db: Session,
    user: User,
    email_plaintext: str,
    request: Request | None,
) -> None:
    """Phase 1b lockout warning email. Imported lazily to avoid a circular
    import (services.email → models.user → services.auth)."""
    from . import site as site_svc
    from .email import send_lockout_warning_email

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
        site_timezone=site_svc.get_site_timezone(db),
        db=db,
    )



# Argon2id is deliberately expensive: at the configured cost (64 MiB, t=3) a
# single verify takes ~0.2s of pure CPU. `authenticate_first_factor` is an
# `async def` on uvicorn's single event loop, so every verify froze the WHOLE
# process - SSE streams, in-flight downloads, every other request - not just the
# caller. Recovery-code login is worse: it loops over up to 10 unused codes.
# Hand them to a thread; the security property (constant-ish latency between the
# unknown-email and wrong-password branches) is unaffected because both branches
# still spend one verify (audit 2026-07-30).
async def _averify(hash_str: str, plaintext: str) -> bool:
    return await asyncio.to_thread(argon2_verify, hash_str, plaintext)

async def authenticate_first_factor(
    db: Session,
    *,
    email: str,
    password: str,
    request: Request | None,
) -> User:
    """Shared pre-second-factor gate for every password-first login path
    (``/api/auth/login``, ``/api/auth/login/recovery``,
    ``/api/auth/webauthn/begin``).

    Runs, in order: per-IP rate limit -> unknown-email -> account lockout ->
    password verify (with failure recording + lockout email) -> disabled ->
    email-not-verified. Returns the authenticated User on success. On every
    failure branch it records the attempt, commits, and raises ``AppError``
    with the same codes/messages as ``login()``. The caller owns the second
    factor (TOTP / recovery / passkey) and the success path. Extracted so the
    password and WebAuthn flows cannot drift apart on throttling/lockout - the
    bug that left /webauthn/begin an unthrottled password + enumeration oracle
    (audit H1)."""
    ip = _request_ip(request)

    # 1. Per-IP rate limit
    if ip and not rate_limit_svc.check_login_ip_allowed(
        ip,
        limit=settings_registry.effective(db, settings_registry.K.RATE_LIMIT_LOGIN),
        window_sec=settings_registry.effective(db, settings_registry.K.LOGIN_RATE_WINDOW_SEC),
    ):
        _record_login_attempt(db, email_value=None, ip=ip, outcome=LoginOutcome.rate_limited)
        db.commit()
        raise AppError(429, "RATE_LIMITED", "Too many login attempts. Try again later.")

    em_email = normalize_email(email)
    user = db.query(User).filter(User.email == em_email).one_or_none()

    # 2. Unknown email -> bad credentials. Spend one Argon2 verify against a
    # fixed dummy hash so the latency matches the wrong-password branch below
    # (no enumeration-by-timing tell).
    if user is None:
        await _averify(_DUMMY_PASSWORD_HASH, password)
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

    # 3. Account lockout (423 with retry-after hint baked into details)
    #
    # The password is verified FIRST, and its result decides which answer the
    # caller gets. Answering 423 on the strength of the address alone was an
    # account-existence oracle: six wrong passwords turned a real account into
    # `423 ACCOUNT_LOCKED` with a `locked_until` timestamp while an unknown
    # address answered 401 all six times - one probe per address, inside a
    # single per-IP window, no timing analysis needed. The same sequence also
    # locked every confirmed account for 15 minutes and mailed it a lockout
    # warning, so the probe doubled as a targeted denial of service and a
    # phishing pretext (audit #2).
    #
    # A locked account with the RIGHT password still gets 423 with its
    # `locked_until` - the honest owner needs to know why they cannot get in.
    # A wrong password gets the same 401 as an address that does not exist.
    locked = rate_limit_svc.is_account_locked(user)
    if locked and not await _averify(user.password_hash, password):
        _record_login_attempt(db, email_value=em_email, ip=ip, outcome=LoginOutcome.locked)
        record_audit_event(
            db,
            event_type=AuditEventType.login_failure,
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            metadata={"reason": "bad_password_while_locked"},
            request=request,
        )
        db.commit()
        raise AppError(401, "INVALID_CREDENTIALS", "Invalid email or password.")
    if locked:
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
    if not await _averify(user.password_hash, password):
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
                logger.exception(
                    "lockout warning email failed for user=%d via bad_password",
                    user.id,
                )
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

    return user


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
    - RATE_LIMITED      - per-IP login rate exceeded
    - INVALID_CREDENTIALS - bad email or bad password
    - ACCOUNT_LOCKED    - account-level lockout active (15min)
    - ACCOUNT_DISABLED
    - EMAIL_NOT_VERIFIED
    - TOTP_REQUIRED     - 2FA is on, totp_code missing
    - INVALID_TOTP      - totp_code wrong (also bumps failure counter)
    """
    user = await authenticate_first_factor(
        db, email=email, password=password, request=request
    )
    ip = _request_ip(request)
    em_email = normalize_email(email)

    # TOTP challenge (if enabled)
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
                        db=db, user=user, email_plaintext=email, request=request
                    )
                    rate_limit_svc.mark_lockout_email_sent(db, user=user)
                except Exception:
                    logger.exception(
                        "lockout warning email failed for user=%d via bad_totp",
                        user.id,
                    )
            db.commit()
            raise AppError(401, "INVALID_TOTP", "Two-factor code is invalid.")

    # Success path
    rate_limit_svc.record_success(db, user=user)
    # Do NOT clear the shared per-IP login window on success: it is keyed by IP
    # (not account), so resetting it would let one valid credential wipe the
    # throttle and brute-force OTHER accounts from the same IP (audit L1). The
    # window expires on its own after LOGIN_RATE_WINDOW_SEC.

    access, expires_in, refresh_plain = finalize_successful_login(
        db, user=user, request=request, settings=settings, via="password",
        email_value=em_email,
    )
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
    user = await authenticate_first_factor(
        db, email=email, password=password, request=request
    )
    ip = _request_ip(request)
    em_email = normalize_email(email)

    if not totp_svc.is_enabled(user):
        # No 2FA configured → recovery codes don't apply. Use /login.
        db.commit()
        raise AppError(409, "TOTP_NOT_ENABLED", "Two-factor auth is not enabled for this account.")

    if not await totp_svc.aconsume_recovery_code(
        db, user=user, code=recovery_code, request=request
    ):
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
    # The shared per-IP window is intentionally not cleared on success (audit L1).

    access, expires_in, refresh_plain = finalize_successful_login(
        db, user=user, request=request, settings=settings, via="recovery_code",
        email_value=em_email,
    )
    return user, access, expires_in, refresh_plain


# `rotate_refresh` and `logout` live in services/jwt_session.py - routers
# call them directly.


def begin_password_reset(
    db: Session, *, email: str, request: Request | None
) -> tuple[User, str] | None:
    """Returns (user, plaintext_token) or None if no user with that email
    exists. Caller awaits the email send. We never reveal account existence
    to the client - the API endpoint always returns 200 regardless.
    """
    em_email = normalize_email(email)
    user = db.query(User).filter(User.email == em_email).one_or_none()
    if user is None or user.is_disabled:
        return None

    plaintext = random_token(32)
    now = utc_now()
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
    if record.expires_at < utc_now():
        raise AppError(410, "RESET_TOKEN_EXPIRED", "Reset link has expired.")

    await assert_password_not_breached(db, new_password)

    # Atomically CLAIM the token: the conditional UPDATE + rowcount check
    # is the single-use gate. Two concurrent requests with the same token
    # both pass the read checks above, but only one wins this UPDATE - the
    # loser gets 410 and never resets the password (finding M6).
    claimed = db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.id == record.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=utc_now())
    )
    if updated_rows(claimed) == 0:
        raise AppError(410, "RESET_TOKEN_USED", "Reset link has already been used.")

    user = db.query(User).filter(User.id == record.user_id).one()
    user.password_hash = argon2_hash(new_password)
    # Completing a reset is proof of control, so clear any prior lockout - else
    # a locked-out user who resets still hits ACCOUNT_LOCKED on next login until
    # the window elapses (reset is not lockout-gated, so this is the only place).
    user.failed_login_count = 0
    user.locked_until = None

    revoke_all_user_refresh_tokens(db, user.id)

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
    now = utc_now()
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
    if record.expires_at < utc_now():
        raise AppError(410, "VERIFY_TOKEN_EXPIRED", "Verification link has expired.")

    # Atomic single-use claim (see consume_password_reset for rationale).
    claimed = db.execute(
        update(EmailVerifyToken)
        .where(
            EmailVerifyToken.id == record.id,
            EmailVerifyToken.used_at.is_(None),
        )
        .values(used_at=utc_now())
    )
    if updated_rows(claimed) == 0:
        raise AppError(410, "VERIFY_TOKEN_USED", "Verification link has already been used.")

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
    # Off the loop, like every other verify in this module: 64 MiB of Argon2id
    # on the event loop freezes the whole process for ~0.2 s.
    if not await _averify(user.password_hash, current_password):
        raise AppError(401, "INVALID_CREDENTIALS", "Current password is incorrect.")
    await assert_password_not_breached(db, new_password)

    user.password_hash = argon2_hash(new_password)
    revoke_all_user_refresh_tokens(db, user.id)
    record_audit_event(
        db,
        event_type=AuditEventType.password_changed,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        request=request,
    )


# Convenience: re-export user role (kept simple - services have flat exports)
_ALL_ROLES = (UserRole.admin, UserRole.employee, UserRole.client)


async def complete_pending_second_factor(
    db: Session,
    *,
    pending_token: str,
    totp_code: str | None,
    recovery_code: str | None,
    request: Request | None,
    settings,
) -> tuple[User, str, int, str]:
    """Exchange a pending-2FA token plus a second factor for a real session.

    The first factor here was OIDC or a passkey, neither of which challenged an
    enrolled TOTP factor: both called finalize_successful_login directly, so a
    user who had switched 2FA on was fully authenticated by one factor. That is
    not a missing branch but a missing STATE - there was no way to represent
    "first factor done" - which is what jwt_session's pending token adds.

    `twofa_policy.is_2fa_required` is deliberately NOT the predicate: it returns
    False once a user HAS TOTP, because it answers "must they still set 2FA up".
    The right question is `totp_svc.is_enabled`, exactly as the password flow
    asks it.

    Recovery codes are accepted, not just TOTP. The password flow has
    /login/recovery as an alternate; without the equivalent here a user who
    loses their authenticator and signs in through SSO has no route back into
    their own account short of an operator on the host.
    """
    user, via = jwt_session.resolve_pending_2fa_token(db, pending_token, settings)
    ip = _request_ip(request)

    # Re-check account state: the pending token was minted in a prior request.
    if rate_limit_svc.is_account_locked(user):
        raise AppError(423, "ACCOUNT_LOCKED", "Account is temporarily locked.")
    if not totp_svc.is_enabled(user):
        # Nothing to challenge - refuse rather than mint a session, because a
        # token issued for a 2FA user should never outlive their 2FA.
        raise AppError(409, "TOTP_NOT_ENABLED", "Two-factor auth is not enabled.")

    if recovery_code:
        ok = await totp_svc.aconsume_recovery_code(
            db, user=user, code=recovery_code, request=request
        )
        outcome, reason = LoginOutcome.bad_recovery, "bad_recovery"
    elif totp_code:
        ok = totp_svc.verify_at_login(db, user=user, code=totp_code)
        outcome, reason = LoginOutcome.bad_totp, "bad_totp"
    else:
        raise AppError(401, "TOTP_REQUIRED", "Two-factor code required.")

    if not ok:
        just_locked, _should_email = rate_limit_svc.record_failure(db, user=user)
        _record_login_attempt(db, email_value=user.email, ip=ip, outcome=outcome)
        record_audit_event(
            db,
            event_type=AuditEventType.login_failure,
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            metadata={"reason": reason, "via": via, "just_locked": just_locked},
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
        db.commit()
        raise AppError(
            401,
            "INVALID_RECOVERY_CODE" if recovery_code else "INVALID_TOTP",
            "Recovery code is invalid." if recovery_code else "Two-factor code is invalid.",
        )

    # Only NOW is the login complete. record_success clears failed_login_count
    # and locked_until, so calling it at the first factor - as the OIDC callback
    # used to - handed a failing second factor a freshly reset lockout counter.
    rate_limit_svc.record_success(db, user=user)
    access, expires_in, refresh_plain = finalize_successful_login(
        db, user=user, request=request, settings=settings, via=f"{via}+2fa",
    )
    return user, access, expires_in, refresh_plain
