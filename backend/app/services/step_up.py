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
admin. See `verify_password_or_403` for why it answers 403 and not 401.
"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.user import User
from ..utils.crypto import argon2_verify

# One bucket for every step-up surface. An attacker who has a session can reach
# all eight, so budgeting them separately would multiply the guesses available.
_BUCKET = "step_up"
_WINDOW_SEC = 900


def verify_password_or_403(
    db: Session, user: User, password: str, *, request: Request | None = None
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

    403 (not 401) on a wrong confirm-password: the admin IS authenticated - this
    is a re-auth gate, not a session failure. A 401 here collides with the SPA's
    global access-token-refresh interceptor, which would silently refresh the
    session and re-submit the action with the same wrong password, masking the
    error (the user saw "nothing happened"). A distinct INVALID_PASSWORD code
    lets the UI show a precise message.

    An SSO-only account has no usable local password hash, so it cannot clear
    this gate - deliberately. The recovery is the same CLI escape hatch the rest
    of the operator surface uses; silently waiving the check for such accounts
    would make the gate opt-out by provisioning method.

    The throttle is keyed on the USER, not the IP, and is not the login lockout
    - see rate_limit.check_user_allowed for why both of those matter.
    """
    from . import rate_limit as rate_limit_svc
    from . import settings_registry
    from .audit import record_audit_event

    limit = int(settings_registry.effective(db, settings_registry.K.LOCKOUT_THRESHOLD))

    # Check BEFORE the hash: argon2 here is 64 MiB per call, so an unthrottled
    # attempt is a memory-cost amplifier as well as a guess.
    if not rate_limit_svc.check_user_allowed(_BUCKET, user.id, limit, _WINDOW_SEC):
        record_audit_event(
            db,
            event_type=AuditEventType.step_up_failed,
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            metadata={"reason": "rate_limited"},
            request=request,
        )
        db.commit()
        raise AppError(429, "RATE_LIMITED", "Too many attempts; try again shortly.")

    if not user.password_hash or not argon2_verify(user.password_hash, password):
        record_audit_event(
            db,
            event_type=AuditEventType.step_up_failed,
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            metadata={"reason": "bad_password"},
            request=request,
        )
        # Commit before raising: AppError aborts the request, so an uncommitted
        # audit row is rolled back and the failure leaves no trace at all -
        # which is the state this whole change exists to end. Same pattern as
        # the TOTP_REQUIRED branch in services/auth.py.
        db.commit()
        raise AppError(403, "INVALID_PASSWORD", "Password incorrect.")

    # Ordinary use must never accumulate toward the limit.
    rate_limit_svc.reset_user_window(_BUCKET, user.id)
