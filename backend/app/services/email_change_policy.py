"""Email-change policy resolvers - the live read layer over the three
``email_change.*`` kv settings. Kept separate from ``services.email_change``
(the orchestrator) so the settings router and ``_me_response`` can read the
policy without importing the heavier service.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from . import settings as settings_svc

VERIFICATION_MODES = ("immediate", "verify_new", "verify_both")
DEFAULT_VERIFICATION_MODE = "verify_new"

OIDC_MODES = ("reset_setpw", "reset_only", "keep")
DEFAULT_OIDC_MODE = "reset_setpw"


def effective_verification_mode(db: Session) -> str:
    """How an email change is confirmed. Falls back to the default when the
    stored value is missing or unrecognised (defensive against hand-edits)."""
    raw = settings_svc.get(db, settings_svc.Keys.EMAIL_CHANGE_VERIFICATION_MODE)
    return raw if raw in VERIFICATION_MODES else DEFAULT_VERIFICATION_MODE


def self_service_enabled(db: Session) -> bool:
    """Whether non-admins may change their own email. Default off."""
    return settings_svc.get_bool(
        db, settings_svc.Keys.EMAIL_CHANGE_SELF_SERVICE, default=False
    )


def effective_oidc_mode(db: Session) -> str:
    """What to do with an OIDC binding on email change."""
    raw = settings_svc.get(db, settings_svc.Keys.EMAIL_CHANGE_OIDC_MODE)
    return raw if raw in OIDC_MODES else DEFAULT_OIDC_MODE
