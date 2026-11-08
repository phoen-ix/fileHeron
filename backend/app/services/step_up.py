"""Re-authentication gate for destructive or secret-revealing admin actions.

An admin access token alone is enough to reach every admin route. That is fine
for reversible work, but three surfaces are not reversible and not equivalent to
"being an admin":

- **config backup export** reads secrets back OUT of an installation that is
  otherwise deliberately write-only (Argon2 password hashes, decrypted-then-
  re-encrypted TOTP seeds, recovery-code hashes, OIDC/webhook/SMTP secrets, and
  with `include_env` the JWT/DB/TUS/S3 secrets themselves),
- **config backup import** replaces users, purges identities, invalidates every
  active share and deletes the bytes,
- **right-to-erasure** anonymises an account irreversibly.

The self-update routes already re-prompt for the password precisely because
"session-hijack abuse of a destructive-by-design action" is the threat; these
three did not, which made the protection inconsistent rather than absent by
design. This module is that same check, lifted out of `routers/admin/system.py`
so one implementation covers all of them.

It is a re-auth gate, not a permission check: the caller IS an authenticated
admin. See `refuse` for why it answers 403 and not 401.

The same gate guards the self-service 2FA surfaces: disabling TOTP and
regenerating recovery codes (`services/totp.py`) check the password through
`verify_password_or_403(final=False)` and the code through `refuse` /
`clear_window`, so a wrong code spends the same per-user window and leaves the
same `step_up_failed` row as a wrong password; adding or removing a passkey
(`routers/webauthn.py`) uses the plain password gate.
"""
from __future__ import annotations

from typing import NoReturn

from fastapi import Request
from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.user import User
from ..utils.crypto import argon2_verify

# One bucket for every step-up surface. An attacker who has a session can reach
# all of them, so budgeting them separately would multiply the guesses available.
_BUCKET = "step_up"
_WINDOW_SEC = 900


def assert_attempt_allowed(db: Session, user: User, *, request: Request | None = None) -> None:
    """Spend one attempt of ``user``'s step-up window, or refuse with 429.

    Called BEFORE the hash: argon2 here is 64 MiB per call, so an unthrottled
    attempt is a memory-cost amplifier as well as a guess. One call per
    request, whatever the request then checks - a gate that verifies a
    password and then a second factor must not spend two.
    """
    from . import rate_limit as rate_limit_svc
    from . import settings_registry

    limit = int(settings_registry.effective(db, settings_registry.K.LOCKOUT_THRESHOLD))
    if rate_limit_svc.check_user_allowed(_BUCKET, user.id, limit, _WINDOW_SEC):
        return
    _audit_failure(db, user, reason="rate_limited", request=request)
    raise AppError(429, "RATE_LIMITED", "Too many attempts; try again shortly.")


def refuse(
    db: Session,
    user: User,
    *,
    reason: str,
    code: str,
    message: str,
    request: Request | None = None,
) -> NoReturn:
    """Record a failed step-up and answer 403.

    403 (not 401) on a wrong secret: the caller IS authenticated - this is a
    re-auth gate, not a session failure. A 401 here collides with the SPA's
    global access-token-refresh interceptor, which would silently refresh the
    session and re-submit the action with the same wrong secret, masking the
    error (the user saw "nothing happened"). A distinct code lets the UI show a
    precise message; the 2FA gates keep ``INVALID_TOTP`` for a wrong code.
    """
    _audit_failure(db, user, reason=reason, request=request)
    raise AppError(403, code, message)


def clear_window(user: User) -> None:
    """Forget the attempts once the WHOLE gate passed - ordinary use must never
    accumulate toward the limit. A gate with two secrets (password, then a TOTP
    or recovery code) calls this after the second one, never between them:
    clearing on the password alone would hand a session that knows the
    password an unthrottled oracle for the code (audit 2026-11-08)."""
    from . import rate_limit as rate_limit_svc

    rate_limit_svc.reset_user_window(_BUCKET, user.id)


def password_matches(user: User, password: str) -> bool:
    """An SSO-only account has no usable local password hash, so it cannot
    clear a step-up gate - deliberately. The recovery is the same CLI escape
    hatch the rest of the operator surface uses; silently waiving the check for
    such accounts would make the gate opt-out by provisioning method."""
    return bool(user.password_hash) and argon2_verify(user.password_hash, password)


def verify_password_or_403(
    db: Session,
    user: User,
    password: str,
    *,
    request: Request | None = None,
    final: bool = True,
) -> None:
    """Confirm ``password`` really belongs to ``user``, under a throttle.

    Takes ``db`` and ``request`` because the previous signature - a pure
    (user, password) function - structurally could not rate-limit, count or
    audit, and none of its eight call sites added any of that. So the gate in
    front of config-backup export (the only admin surface that reads secrets
    back out), right-to-erasure, API-token minting and self-update was an
    unlimited, unlogged password oracle: a hijacked session could guess
    forever, and each guess was a 64 MiB Argon2id verify, on the event loop at
    one call site.

    ``final=False`` is for a gate that checks a second secret after the
    password (disabling TOTP, regenerating recovery codes): the attempt is
    spent and a wrong password refused exactly the same way, but the window is
    left for the caller to clear with `clear_window` once the second secret
    passed too.

    The throttle is keyed on the USER, not the IP, and is not the login lockout
    - see rate_limit.check_user_allowed for why both of those matter.
    """
    assert_attempt_allowed(db, user, request=request)
    if not password_matches(user, password):
        refuse(
            db,
            user,
            reason="bad_password",
            code="INVALID_PASSWORD",
            message="Password incorrect.",
            request=request,
        )
    if final:
        clear_window(user)


def _audit_failure(db: Session, user: User, *, reason: str, request: Request | None) -> None:
    from .audit import record_audit_event

    record_audit_event(
        db,
        event_type=AuditEventType.step_up_failed,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        metadata={"reason": reason},
        request=request,
    )
    # Commit before raising: AppError aborts the request, so an uncommitted
    # audit row is rolled back and the failure leaves no trace at all -
    # which is the state this whole change exists to end. Same pattern as
    # the TOTP_REQUIRED branch in services/auth.py.
    db.commit()
