"""TOTP / recovery-code service.

Flow:
1. begin_setup(user) → generate fresh base32 secret, encrypt at rest, create
   or overwrite pending UserTOTP row (only if 2FA not already enabled). Returns
   (secret_b32, otpauth_uri, qr_svg). The plaintext secret is shown ONCE here
   so the authenticator app can be paired; never returned again.

2. confirm_enable(user, code) → user enters the first valid TOTP code, we mark
   enabled_at + generate 10 recovery codes (returned plaintext, one-time).

3. verify_at_login(user, code) → at login time. Decrypts secret, validates code
   with ±1 step window, refuses replay (counter ≤ stored last_used_counter).

4. consume_recovery_code(user, code) → iterates unused recovery codes,
   Argon2-verifies. On match, marks used_at, returns True.

5. disable(user, password, code_or_recovery) → password + a current TOTP or
   recovery code. Deletes UserTOTP row + recovery codes (cascade by FK).

6. regenerate_recovery_codes(user, password, code_or_recovery) → invalidates
   the existing 10 codes, returns 10 new plaintexts (one-time).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import pyotp
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..config import settings
from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.user import User
from ..models.user_recovery_code import UserRecoveryCode
from ..models.user_totp import UserTOTP
from ..utils.crypto import (
    SecretUndecryptableError,
    argon2_hash,
    argon2_verify,
    constant_time_equals,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_codes,
)
from ..utils.dbresult import updated_rows
from ..utils.qr import render_qr_svg
from ..utils.timeutil import utc_now
from .audit import record_audit_event

logger = logging.getLogger("fileheron.totp")

# Acceptance window in 30s steps either side of the current step. 2 ⇒ ±60s,
# which tolerates mild authenticator-device clock drift (the common cause of
# "valid code rejected") without meaningfully weakening 2FA - the anti-replay
# counter in verify_at_login still allows only one login per server 30s window.
_TOTP_VALID_WINDOW = 2




def _build_otpauth_uri(secret_b32: str, account_label: str) -> str:
    issuer = settings.APP_NAME
    return pyotp.totp.TOTP(secret_b32).provisioning_uri(
        name=account_label,
        issuer_name=issuer,
    )


def is_enabled(user: User) -> bool:
    return user.totp is not None and user.totp.enabled_at is not None


def begin_setup(db: Session, *, user: User, request) -> dict:
    """Returns {secret_b32, otpauth_uri, qr_svg}. Does NOT activate 2FA - the
    caller must follow up with confirm_enable.
    """
    if is_enabled(user):
        raise AppError(
            409,
            "TOTP_ALREADY_ENABLED",
            "Two-factor auth is already on. Disable it before setting up a new device.",
        )

    secret = pyotp.random_base32()
    label = f"{user.display_name} ({user.email})"
    otpauth_uri = _build_otpauth_uri(secret, label)
    qr_svg = render_qr_svg(otpauth_uri)

    # Replace any pending row.
    if user.totp is not None:
        db.delete(user.totp)
        db.flush()

    row = UserTOTP(
        user_id=user.id,
        secret_encrypted=encrypt_totp_secret(secret),
        enabled_at=None,
        last_used_counter=0,
    )
    db.add(row)
    db.flush()
    record_audit_event(
        db,
        event_type="totp_setup_started",
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        request=request,
    )
    return {"secret_b32": secret, "otpauth_uri": otpauth_uri, "qr_svg": qr_svg}


def _decrypt_or_503(totp: UserTOTP) -> str:
    """Both TOTP paths decrypt the stored secret. An undecryptable secret -
    JWT_SECRET rotated without running scripts/rotate_jwt_secret.py - used to
    escape as a raw InvalidToken and 500 the login, which tells the user
    nothing and the operator less. Fail with a code that names the cause
    (audit 2026-07-30)."""
    try:
        return decrypt_totp_secret(totp.secret_encrypted)
    except SecretUndecryptableError:
        logger.error(
            "totp: secret for user_id=%s could not be decrypted; "
            "JWT_SECRET was probably rotated without re-encrypting",
            totp.user_id,
        )
        raise AppError(
            503,
            "TOTP_SECRET_UNAVAILABLE",
            "Your two-factor secret cannot be read. An administrator must "
            "re-enrol your device.",
        ) from None


def confirm_enable(db: Session, *, user: User, code: str, request) -> list[str]:
    """User submits the first valid code. Marks 2FA active and returns the
    one-time list of plaintext recovery codes (caller MUST display these once
    only).
    """
    if user.totp is None:
        raise AppError(409, "TOTP_SETUP_MISSING", "Begin setup first via /api/account/2fa/setup.")
    if user.totp.enabled_at is not None:
        raise AppError(409, "TOTP_ALREADY_ENABLED", "Two-factor auth is already on.")

    secret = _decrypt_or_503(user.totp)
    if not pyotp.TOTP(secret).verify(code, valid_window=_TOTP_VALID_WINDOW):
        raise AppError(401, "INVALID_TOTP", "Code is incorrect or expired.")

    user.totp.enabled_at = utc_now()
    # NOTE: do NOT seed last_used_counter to the current step. The first login
    # after enable would otherwise be rejected as replay until the next 30s
    # window. Anti-replay only kicks in once a code is accepted by login.

    # Replace any existing recovery codes (defensive - should be none).
    for rc in list(user.recovery_codes):
        db.delete(rc)
    db.flush()

    plaintexts = generate_recovery_codes(10)
    for plain in plaintexts:
        db.add(UserRecoveryCode(user_id=user.id, code_hash=argon2_hash(plain)))
    db.flush()

    record_audit_event(
        db,
        event_type=AuditEventType.totp_enabled,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        request=request,
    )
    return plaintexts


def _matched_step(totp: pyotp.TOTP, code: str) -> int | None:
    """Which 30-second step the accepted code came from.

    pyotp.verify() tells us a code is valid somewhere in +/-_TOTP_VALID_WINDOW
    but not where, and the anti-replay guard needs the where. Walks the window
    oldest-first so a code that is somehow valid at two steps is pinned to the
    earliest, which is the conservative choice for a monotonic counter."""
    now = int(datetime.now(tz=timezone.utc).timestamp())
    for offset in range(-_TOTP_VALID_WINDOW, _TOTP_VALID_WINDOW + 1):
        step = now // 30 + offset
        if constant_time_equals(totp.at(step * 30), code):
            return step
    return None


def verify_at_login(db: Session, *, user: User, code: str) -> bool:
    """Returns True iff the code is valid AND not a replay. Updates
    last_used_counter on success.

    Anti-replay uses an atomic conditional UPDATE so two concurrent
    requests presenting the same code in the same 30s window can't
    both succeed - only the first wins, the rest get rowcount=0.
    """
    if user.totp is None or user.totp.enabled_at is None:
        return False
    secret = _decrypt_or_503(user.totp)
    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=_TOTP_VALID_WINDOW):
        return False

    # Record the step the submitted code BELONGS to, not the step the server is
    # currently in. Storing the current step only blocked replay within that
    # same 30s window: a code from step N-2 or N-1 (both inside the +/-2
    # tolerance window) was accepted, stamped with step N, and then remained
    # replayable at step N+1 because N < N+1 still satisfies the guard. A
    # captured code therefore stayed usable for roughly another minute after
    # its first successful use - which is precisely the window a shoulder-surfed
    # or phished code is worth something in (audit 2026-07-30).
    matched_step = _matched_step(totp, code)
    if matched_step is None:  # pragma: no cover - verify() just said it matches
        return False
    result = db.execute(
        update(UserTOTP)
        .where(
            UserTOTP.user_id == user.id,
            UserTOTP.last_used_counter < matched_step,
        )
        .values(last_used_counter=matched_step)
    )
    db.flush()
    if updated_rows(result) == 1:
        db.refresh(user.totp)
        return True
    return False


def _unused_recovery_hashes(db: Session, user: User) -> list[tuple[int, str]]:
    """(id, hash) pairs, deliberately NOT ORM rows: the matching runs in a
    worker thread and touching a mapped object there could emit SQL on a
    Session another thread owns."""
    return [
        (rc.id, rc.code_hash)
        for rc in db.query(UserRecoveryCode).filter(
            UserRecoveryCode.user_id == user.id,
            UserRecoveryCode.used_at.is_(None),
        )
    ]


def _match_recovery_hash(pairs: list[tuple[int, str]], code: str) -> int | None:
    """Pure CPU, no DB, no ORM - safe to hand to a thread. Returns the matching
    row id, or None."""
    for rc_id, code_hash in pairs:
        if argon2_verify(code_hash, code):
            return rc_id
    return None


def _claim_recovery_code(db: Session, *, user: User, rc_id: int, request) -> bool:
    # Atomic single-use claim: the conditional UPDATE + rowcount
    # check is the gate, so two concurrent logins replaying the
    # same recovery code can't both succeed (finding M6).
    claimed = db.execute(
        update(UserRecoveryCode)
        .where(
            UserRecoveryCode.id == rc_id,
            UserRecoveryCode.used_at.is_(None),
        )
        .values(used_at=utc_now())
    )
    if updated_rows(claimed) == 0:
        # Lost the race - another request already consumed it.
        return False
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.recovery_code_used,
        actor_user_id=user.id,
        target_type="user_recovery_code",
        target_id=rc_id,
        request=request,
    )
    return True


def consume_recovery_code(db: Session, *, user: User, code: str, request) -> bool:
    """Match `code` against any unused recovery code for `user`. On match,
    marks used and audit-logs. Returns whether match was found.

    Synchronous, for the two sync callers below. Anything on the event loop
    must use `aconsume_recovery_code`."""
    rc_id = _match_recovery_hash(_unused_recovery_hashes(db, user), code)
    return _claim_recovery_code(db, user=user, rc_id=rc_id, request=request) if rc_id else False


async def aconsume_recovery_code(db: Session, *, user: User, code: str, request) -> bool:
    """`consume_recovery_code` with the Argon2 work off the event loop.

    Ten codes are minted per user and a WRONG code verifies every one of them:
    at the shipped parameters (64 MiB, t=3, p=2) that is roughly two seconds
    with the whole process frozen, on an endpoint reachable before the second
    factor. `services/auth.py` already documents this exact case - "recovery-code
    login is worse: it loops over up to 10 unused codes" - and then wired the
    thread into the password path only.

    Only the matching is threaded. The claim stays on the caller's thread
    because it owns the Session."""
    rc_id = await asyncio.to_thread(
        _match_recovery_hash, _unused_recovery_hashes(db, user), code
    )
    return _claim_recovery_code(db, user=user, rc_id=rc_id, request=request) if rc_id else False


def disable(db: Session, *, user: User, password: str, code_or_recovery: str, request) -> None:
    """Disable 2FA. Requires password + (current TOTP code OR recovery code).
    Deletes UserTOTP row + all recovery codes (cascade)."""
    if not argon2_verify(user.password_hash, password):
        raise AppError(401, "INVALID_CREDENTIALS", "Password incorrect.")
    if user.totp is None or user.totp.enabled_at is None:
        raise AppError(409, "TOTP_NOT_ENABLED", "Two-factor auth is not enabled.")

    # A recovery code alone is enough when the SECRET cannot be decrypted -
    # which is the JWT_SECRET-rotated-without-re-encrypting case
    # `SecretUndecryptableError` exists to name. Before this, `verify_at_login`
    # raised 503 out of the `or`, so the user could sign in with a recovery code
    # but could not disable 2FA, could not mint new recovery codes, and no admin
    # endpoint could clear it: after the tenth code the account was permanently
    # unreachable through the API, and the 503's own message named an admin
    # remedy that does not exist (audit #2).
    try:
        totp_ok = verify_at_login(db, user=user, code=code_or_recovery)
    except AppError as e:
        if e.code != "TOTP_SECRET_UNAVAILABLE":
            raise
        logger.warning(
            "totp: disabling 2FA for user_id=%s on a recovery code alone - the "
            "stored secret cannot be decrypted",
            user.id,
        )
        totp_ok = False
    if not (
        totp_ok
        or consume_recovery_code(db, user=user, code=code_or_recovery, request=request)
    ):
        raise AppError(401, "INVALID_TOTP", "Provide a valid TOTP code or recovery code.")

    db.delete(user.totp)
    for rc in list(user.recovery_codes):
        db.delete(rc)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.totp_disabled,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        request=request,
    )


def regenerate_recovery_codes(
    db: Session, *, user: User, password: str, code_or_recovery: str, request
) -> list[str]:
    """Replace all existing recovery codes. Returns 10 new plaintexts (once).
    Same auth gate as `disable`."""
    if not argon2_verify(user.password_hash, password):
        raise AppError(401, "INVALID_CREDENTIALS", "Password incorrect.")
    if user.totp is None or user.totp.enabled_at is None:
        raise AppError(409, "TOTP_NOT_ENABLED", "Two-factor auth is not enabled.")
    # Same reasoning as `disable`: an undecryptable secret must not be able to
    # lock a user out of minting fresh recovery codes (audit #2).
    try:
        totp_ok = verify_at_login(db, user=user, code=code_or_recovery)
    except AppError as e:
        if e.code != "TOTP_SECRET_UNAVAILABLE":
            raise
        totp_ok = False
    if not (
        totp_ok
        or consume_recovery_code(db, user=user, code=code_or_recovery, request=request)
    ):
        raise AppError(401, "INVALID_TOTP", "Provide a valid TOTP code or recovery code.")

    for rc in list(user.recovery_codes):
        db.delete(rc)
    db.flush()
    plaintexts = generate_recovery_codes(10)
    for plain in plaintexts:
        db.add(UserRecoveryCode(user_id=user.id, code_hash=argon2_hash(plain)))
    db.flush()
    return plaintexts


