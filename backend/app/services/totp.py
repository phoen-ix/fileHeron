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
    argon2_hash,
    argon2_verify,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_codes,
)
from ..utils.qr import render_qr_svg
from ..utils.timeutil import utc_now
from .audit import record_audit_event

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


def confirm_enable(db: Session, *, user: User, code: str, request) -> list[str]:
    """User submits the first valid code. Marks 2FA active and returns the
    one-time list of plaintext recovery codes (caller MUST display these once
    only).
    """
    if user.totp is None:
        raise AppError(409, "TOTP_SETUP_MISSING", "Begin setup first via /api/account/2fa/setup.")
    if user.totp.enabled_at is not None:
        raise AppError(409, "TOTP_ALREADY_ENABLED", "Two-factor auth is already on.")

    secret = decrypt_totp_secret(user.totp.secret_encrypted)
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


def verify_at_login(db: Session, *, user: User, code: str) -> bool:
    """Returns True iff the code is valid AND not a replay. Updates
    last_used_counter on success.

    Anti-replay uses an atomic conditional UPDATE so two concurrent
    requests presenting the same code in the same 30s window can't
    both succeed - only the first wins, the rest get rowcount=0.
    """
    if user.totp is None or user.totp.enabled_at is None:
        return False
    secret = decrypt_totp_secret(user.totp.secret_encrypted)
    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=_TOTP_VALID_WINDOW):
        return False

    current_counter = int(datetime.now(tz=timezone.utc).timestamp() // 30)
    result = db.execute(
        update(UserTOTP)
        .where(
            UserTOTP.user_id == user.id,
            UserTOTP.last_used_counter < current_counter,
        )
        .values(last_used_counter=current_counter)
    )
    db.flush()
    if result.rowcount == 1:
        db.refresh(user.totp)
        return True
    return False


def consume_recovery_code(db: Session, *, user: User, code: str, request) -> bool:
    """Match `code` against any unused recovery code for `user`. On match,
    marks used and audit-logs. Returns whether match was found."""
    candidates = (
        db.query(UserRecoveryCode)
        .filter(UserRecoveryCode.user_id == user.id, UserRecoveryCode.used_at.is_(None))
        .all()
    )
    for rc in candidates:
        if argon2_verify(rc.code_hash, code):
            # Atomic single-use claim: the conditional UPDATE + rowcount
            # check is the gate, so two concurrent logins replaying the
            # same recovery code can't both succeed (finding M6).
            claimed = db.execute(
                update(UserRecoveryCode)
                .where(
                    UserRecoveryCode.id == rc.id,
                    UserRecoveryCode.used_at.is_(None),
                )
                .values(used_at=utc_now())
            )
            if claimed.rowcount == 0:
                # Lost the race - another request already consumed it.
                return False
            db.flush()
            record_audit_event(
                db,
                event_type=AuditEventType.recovery_code_used,
                actor_user_id=user.id,
                target_type="user_recovery_code",
                target_id=rc.id,
                request=request,
            )
            return True
    return False


def disable(db: Session, *, user: User, password: str, code_or_recovery: str, request) -> None:
    """Disable 2FA. Requires password + (current TOTP code OR recovery code).
    Deletes UserTOTP row + all recovery codes (cascade)."""
    if not argon2_verify(user.password_hash, password):
        raise AppError(401, "INVALID_CREDENTIALS", "Password incorrect.")
    if user.totp is None or user.totp.enabled_at is None:
        raise AppError(409, "TOTP_NOT_ENABLED", "Two-factor auth is not enabled.")

    if not (
        verify_at_login(db, user=user, code=code_or_recovery)
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
    if not (
        verify_at_login(db, user=user, code=code_or_recovery)
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


