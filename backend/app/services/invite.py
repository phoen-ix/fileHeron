"""Invite token lifecycle: create / consume.

Tokens are 32 bytes urlsafe-base64 random; only the SHA-256 hash is stored.
Default expiry is 24h. Sending the email is the caller's job.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.invite_token import InviteToken
from ..models.user import User, UserRole
from ..utils.crypto import normalize_email, random_token, sha256_hex


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
