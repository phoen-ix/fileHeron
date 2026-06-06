"""Request-time gate that blocks routes when the active 2FA policy
requires the user to have TOTP and they don't.

The policy is computed live by `services.twofa_policy.is_2fa_required`
on every request - no flag column, no boot-time walk. The gate is a
FastAPI dependency mounted on the protected routers in `main.py`;
`/api/account/2fa/*`, `/api/account/me`, `/api/auth/*`,
`/api/health`, and the public/anonymous routers are exempt so the
user can always read /me, log in, and complete setup.

API-token requests short-circuit: tokens are session-less machine
credentials, trusted at the moment of issuance. If admin tightens
policy after a token was minted, the existing token keeps working
and the operator can audit + revoke it via the admin inventory.
"""
from __future__ import annotations

import logging

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..dependencies import get_actor, get_db
from ..middleware.errors import AppError
from ..models.user import User
from .twofa_policy import is_2fa_required

logger = logging.getLogger("fileheron.twofa_enforcement")


def require_2fa_complete(
    request: Request,
    user: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> None:
    """Raise 403 TWOFA_SETUP_REQUIRED when the active policy requires
    2FA for this user (JWT session) and they haven't enabled it.

    API-token authenticated requests bypass the gate (see module
    docstring)."""
    if getattr(request.state, "auth_via", None) == "api_token":
        return
    if is_2fa_required(db, user):
        raise AppError(
            403,
            "TWOFA_SETUP_REQUIRED",
            "Two-factor authentication is required. Set it up to continue.",
        )
