"""Invite token lifecycle: create / consume / list / revoke / regenerate /
resend / admin-activate.

Tokens are 32 bytes urlsafe-base64 random; only the SHA-256 hash is stored.
Default expiry is 24h. Sending the email is the caller's job (for create);
``resend_invite`` re-sends through the same dispatch path.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.invite_token import InviteToken
from ..models.user import Locale, User, UserRole
from ..utils.crypto import normalize_email, random_token, sha256_hex
from .audit import record_audit_event


def _utcnow() -> datetime:
    # Naive UTC — matches what MariaDB returns for DATETIME columns.
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def create_invite(
    db: Session,
    *,
    email: str,
    target_role: UserRole,
    created_by: User,
    initial_group_ids: list[int] | None = None,
    ttl: timedelta = timedelta(hours=24),
) -> tuple[InviteToken, str]:
    """Create a new invite. Returns (record, plaintext_token). The plaintext
    is only available here — store the hash, send the plaintext over email.

    `initial_group_ids` (optional): groups the invitee is added to on
    consume. Caller is responsible for validating that the IDs exist.
    """
    plaintext = random_token(32)
    expires_at = _utcnow() + ttl

    record = InviteToken(
        token_hash=sha256_hex(plaintext),
        email=normalize_email(email),
        target_role=target_role,
        created_by_id=created_by.id,
        expires_at=expires_at,
        initial_group_ids=list(initial_group_ids) if initial_group_ids else None,
    )
    db.add(record)
    db.flush()
    return record, plaintext


def has_pending_invite(db: Session, *, email_value: str) -> bool:
    """True if there's an unused, unexpired invite for this email.
    Used by the invite route to refuse duplicates."""
    row = (
        db.query(InviteToken)
        .filter(
            InviteToken.email == email_value,
            InviteToken.used_at.is_(None),
            InviteToken.expires_at > _utcnow(),
        )
        .first()
    )
    return row is not None


def consume_invite(db: Session, *, plaintext_token: str) -> InviteToken:
    """Look up an invite by plaintext token. Returns the record on success.
    Raises AppError if missing / used / expired.
    """
    record = (
        db.query(InviteToken)
        .filter(InviteToken.token_hash == sha256_hex(plaintext_token))
        .one_or_none()
    )
    if record is None:
        raise AppError(404, "INVITE_INVALID", "Invite is invalid.")
    if record.used_at is not None:
        raise AppError(410, "INVITE_USED", "Invite has already been used.")
    if record.expires_at < _utcnow():
        raise AppError(410, "INVITE_EXPIRED", "Invite has expired.")
    return record


def mark_invite_consumed(db: Session, record: InviteToken, used_user_id: int) -> None:
    record.used_at = _utcnow()
    record.used_user_id = used_user_id
    db.flush()


# ---------------------------------------------------------------------------
# Admin views over pending / expired invites (post-Phase 10).
#
# An invite has three observable states:
#   - pending: used_at IS NULL AND expires_at > now()
#   - expired: used_at IS NULL AND expires_at <= now()
#   - tombstoned (revoked): used_at IS NOT NULL AND used_user_id IS NULL
#   - consumed: used_at IS NOT NULL AND used_user_id IS NOT NULL
#
# The admin-list endpoint returns pending + expired only — tombstoned and
# consumed rows are excluded by filtering on ``used_at IS NULL``.
# ---------------------------------------------------------------------------


def list_invites(
    db: Session,
    *,
    state_filter: str = "all",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[InviteToken], int]:
    """List invites that have NOT been consumed by a real user.

    The base filter is ``used_user_id IS NULL`` — this captures three
    visible states:

    - **pending**:  ``used_at IS NULL AND expires_at > now()``
    - **expired**:  ``used_at IS NULL AND expires_at <= now()``
    - **revoked**:  ``used_at IS NOT NULL`` (tombstone — admin
                    cancelled the invite before it was consumed)

    ``state_filter``: ``"pending"`` | ``"expired"`` | ``"revoked"`` |
    ``"all"``. Returns ``(items, total)``.
    """
    now = _utcnow()
    base = db.query(InviteToken).filter(InviteToken.used_user_id.is_(None))
    if state_filter == "pending":
        base = base.filter(
            InviteToken.used_at.is_(None), InviteToken.expires_at > now
        )
    elif state_filter == "expired":
        base = base.filter(
            InviteToken.used_at.is_(None), InviteToken.expires_at <= now
        )
    elif state_filter == "revoked":
        base = base.filter(InviteToken.used_at.is_not(None))
    # else "all": no extra filter

    total = base.count()
    items = (
        base.order_by(InviteToken.created_at.desc())
        .offset(max(0, (page - 1)) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


def revoke_invite(
    db: Session, *, invite: InviteToken, actor: User, request=None
) -> None:
    """Tombstone an invite: ``used_at`` set, ``used_user_id`` left NULL.

    Soft-tombstone instead of hard-delete so the audit trail (who
    revoked, when) survives. The list endpoint filters on
    ``used_at IS NULL`` so tombstoned rows disappear from the UI
    without further work.
    """
    if invite.used_at is not None and invite.used_user_id is not None:
        raise AppError(
            409,
            "INVITE_ALREADY_CONSUMED",
            "Invite has already been consumed and cannot be revoked.",
        )
    invite.used_at = _utcnow()
    invite.used_user_id = None  # discriminates revoked from consumed
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.invite_revoked,
        actor_user_id=actor.id,
        target_type="invite",
        target_id=invite.id,
        metadata={
            "email": invite.email,
            "target_role": invite.target_role.value,
        },
        request=request,
    )


def regenerate_invite(
    db: Session,
    *,
    invite: InviteToken,
    actor: User,
    ttl: timedelta = timedelta(hours=24),
    request=None,
    audit: bool = True,
) -> str:
    """Mint a fresh plaintext token on an existing invite row.

    Refuses if the invite has been consumed (used_at IS NOT NULL AND
    used_user_id IS NOT NULL). Allowed for pending and expired and
    revoked-but-not-consumed states (revoking an admin's prior decision
    is fine — that's an undo). Resets ``created_at`` and pushes
    ``expires_at = now + ttl``. Returns the new plaintext, which the
    caller must surface exactly once.

    ``audit=False`` suppresses the ``invite_created`` audit row so a
    composite caller (``resend_invite``) can emit a single combined
    audit event with ``resent=true`` instead of two separate rows.
    """
    if invite.used_at is not None and invite.used_user_id is not None:
        raise AppError(
            409,
            "INVITE_ALREADY_CONSUMED",
            "Invite has been consumed; create a fresh invite instead.",
        )
    plaintext = random_token(32)
    now = _utcnow()
    invite.token_hash = sha256_hex(plaintext)
    invite.created_at = now
    invite.expires_at = now + ttl
    invite.used_at = None  # un-tombstone if it was revoked
    invite.used_user_id = None
    db.flush()
    if audit:
        record_audit_event(
            db,
            event_type=AuditEventType.invite_created,
            actor_user_id=actor.id,
            target_type="invite",
            target_id=invite.id,
            metadata={
                "email": invite.email,
                "target_role": invite.target_role.value,
                "regenerated_from": invite.id,
                "expires_at": invite.expires_at.isoformat(),
            },
            request=request,
        )
    return plaintext


async def resend_invite(
    db: Session,
    *,
    invite: InviteToken,
    actor: User,
    ttl: timedelta = timedelta(hours=24),
    request=None,
) -> datetime:
    """Mint a fresh plaintext + email it to the invitee.

    Uses the same template + dispatch funnel as the original create-invite
    flow (``send_invite_email``). Until SMTP is wired the mail body lands
    in backend stdout — same behaviour as the create flow, so admins who
    are familiar with that workflow can still recover the link.

    Returns the new ``expires_at``. The plaintext token never leaves
    this service.
    """
    from . import email as email_svc
    from . import site as site_svc

    # Audit suppressed in regenerate so we emit a single combined row
    # below with `resent=true`.
    plaintext = regenerate_invite(
        db, invite=invite, actor=actor, ttl=ttl, request=request, audit=False
    )
    record_audit_event(
        db,
        event_type=AuditEventType.invite_created,
        actor_user_id=actor.id,
        target_type="invite",
        target_id=invite.id,
        metadata={
            "email": invite.email,
            "target_role": invite.target_role.value,
            "regenerated_from": invite.id,
            "resent": True,
            "expires_at": invite.expires_at.isoformat(),
        },
        request=request,
    )
    # Display-name hint isn't stored on the invite row; use the local
    # part of the email so the rendered greeting reads "Hello tina"
    # rather than the email itself.
    hint = invite.email.split("@", 1)[0]
    await email_svc.send_invite_email(
        to=invite.email,
        locale=actor.locale,
        display_name_hint=hint,
        inviter_display_name=actor.display_name,
        token=plaintext,
        app_url=site_svc.get_site_url(db),
    )
    return invite.expires_at


def activate_invite_as_admin(
    db: Session,
    *,
    invite: InviteToken,
    actor: User,
    display_name: str | None = None,
    locale: Locale | None = None,
    request=None,
) -> User:
    """Bypass the invitee's password-set step and activate the account
    directly.

    Server generates a placeholder password (a 32-byte urlsafe-base64
    string, never returned, only the Argon2 hash is stored). The user
    can never authenticate with this password — admins must rely on
    SSO or a follow-up "force password reset" if a usable local
    password is needed.

    Refuses on:
      - INVITE_ALREADY_CONSUMED if the invite has been consumed already
      - USER_EXISTS if a real user already exists for invite.email
        (raised inside _create_user_from_invite)

    Allowed on expired invites — admin override.
    Allowed on revoked invites — same row, just resurrect it.
    """
    from .auth import _create_user_from_invite

    if invite.used_at is not None and invite.used_user_id is not None:
        raise AppError(
            409,
            "INVITE_ALREADY_CONSUMED",
            "Invite has already been consumed.",
        )
    # If the invite was revoked (tombstoned) earlier, undo that so the
    # shared creator can mark it consumed against the new user.
    if invite.used_at is not None and invite.used_user_id is None:
        invite.used_at = None
    if display_name is None:
        local = invite.email.split("@", 1)[0]
        # Replace _/. separators with spaces and title-case so
        # `tina.treutler` → "Tina Treutler".
        display_name = local.replace("_", " ").replace(".", " ").title()
    if locale is None:
        locale = actor.locale
    placeholder_password = random_token(32)
    user = _create_user_from_invite(
        db,
        invite=invite,
        password=placeholder_password,
        display_name=display_name,
        locale=locale,
        via="admin_direct",
        request=request,
    )
    return user
