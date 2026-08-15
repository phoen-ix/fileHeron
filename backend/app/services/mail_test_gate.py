"""Guard for the SMTP/IMAP "Test connection" routes.

Both routes exist so an admin can test the settings they have ON SCREEN rather
than the ones already saved, and both fill in a blank password from the stored
config so the form never has to round-trip the secret. Those two conveniences
compose into a credential-exfiltration primitive: point the host at a server you
control, leave the password blank, and the installation hands over the org's
mail password. Measured against the real routes: the stored secret arrived at
`mx.attacker.tld:2525` in cleartext.

`utils/net.assert_safe_host` does not help. It is an ADDRESS policy - it rejects
loopback, link-local and metadata addresses - with `allow_private=True`, and it
fails open on a name that does not resolve. Any public host passes. It was never
the control for this.

The gate is the INTERSECTION of two conditions, not either one:

  * we would substitute the STORED secret (the caller left the password blank),
    and
  * the target is not the saved one.

Either alone is harmless. Re-testing the saved server exposes nothing new.
Testing a brand-new server with a password the admin just typed exposes nothing
either - and that second case is exactly why the override exists (try a new
provider before saving it), so gating on host mismatch alone would break the
feature's main purpose while adding no security.

The practical effect is that the prompt appears only when someone asks this
installation to send its saved credential somewhere it has never sent it - a
rare, deliberate act - and never on the everyday "did my config work?" click.
"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.user import User
from .audit import record_audit_event
from .step_up import verify_password_or_403


def guard_and_audit(
    db: Session,
    *,
    admin: User,
    request: Request,
    event_type: AuditEventType,
    target_id: str,
    confirm_password: str | None,
    reuses_stored_secret: bool,
    target_matches_persisted: bool,
    host: str,
    port: int,
    tls_mode: str | None,
) -> None:
    """Gate and record one connection test.

    Raises 403 STEP_UP_REQUIRED when re-auth is needed but absent, and whatever
    `verify_password_or_403` raises when it is present and wrong - including its
    429 once the per-user throttle trips.

    STEP_UP_REQUIRED is a distinct code, not a bare INVALID_PASSWORD, so the SPA
    can reveal the password field on demand instead of having to predict whether
    the form diverged from the saved config. It also reads correctly to a caller
    outside the SPA, which "Password incorrect" would not when no password was
    sent at all.

    Every test aimed at a server OTHER than the saved one is recorded, including
    the refusals - a refused attempt to point this installation's stored
    credential at an attacker-controlled host is the single most interesting
    thing that can happen here, and leaving it untraced would repeat the exact
    gap this gate was written to close. Tests against the saved config are not
    recorded; they are routine and would bury the rest.

    The row is committed BEFORE any raise, because AppError aborts the request
    and an uncommitted row rolls back - the same reason services/step_up.py
    commits its own failures inline.
    """
    if target_matches_persisted:
        return

    exposes_stored_secret = reuses_stored_secret

    if exposes_stored_secret and not confirm_password:
        _record(db, admin=admin, request=request, event_type=event_type,
                target_id=target_id, host=host, port=port, tls_mode=tls_mode,
                outcome="refused_step_up_required")
        db.commit()
        raise AppError(
            403,
            "STEP_UP_REQUIRED",
            "Re-enter your password to test a different server using the stored credentials.",
        )

    if exposes_stored_secret:
        # Raises on a wrong password, after writing its own step_up_failed row.
        verify_password_or_403(db, admin, confirm_password or "", request=request)
        outcome = "allowed_after_step_up"
    else:
        # The caller supplied a password, so nothing stored leaves the box.
        outcome = "allowed_own_credentials"

    _record(db, admin=admin, request=request, event_type=event_type,
            target_id=target_id, host=host, port=port, tls_mode=tls_mode,
            outcome=outcome)
    db.commit()


def _record(
    db: Session,
    *,
    admin: User,
    request: Request,
    event_type: AuditEventType,
    target_id: str,
    host: str,
    port: int,
    tls_mode: str | None,
    outcome: str,
) -> None:
    record_audit_event(
        db,
        event_type=event_type,
        actor_user_id=admin.id,
        target_type="settings",
        target_id=target_id,
        # The forensic value is the TARGET, not that a test happened.
        metadata={"host": host, "port": port, "tls_mode": tls_mode, "outcome": outcome},
        request=request,
    )
