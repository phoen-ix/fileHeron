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

from ..middleware.errors import AppError
from ..models.user import User
from ..utils.crypto import argon2_verify


def verify_password_or_403(user: User, password: str) -> None:
    """Confirm ``password`` really belongs to ``user``.

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
    """
    if not user.password_hash or not argon2_verify(user.password_hash, password):
        raise AppError(403, "INVALID_PASSWORD", "Password incorrect.")
