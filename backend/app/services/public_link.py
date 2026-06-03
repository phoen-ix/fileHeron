"""Public-link lifecycle: create / unlock (password-gated) / counter
decrement / revoke.

Token format: 32 random urlsafe-base64 bytes (43 chars). Stored as
SHA-256 hex; the plaintext is shown to the creator exactly once.

Counter: `downloads_remaining` is the source of truth and is decremented
atomically via a conditional UPDATE (`WHERE downloads_remaining > 0`).
The session sees a fresh row only after re-querying — callers that
need the post-decrement value should re-fetch.

Password rate limit: per-(link, ip), counted from
`public_link_password_attempts` rows in the last
`PUBLIC_LINK_PASSWORD_WINDOW_SEC` window. Hitting the cap sets
`locked_until` on the link itself (so all IPs are blocked, not just the
attacking one) — defense against distributed brute-forcing.
"""
from __future__ import annotations

import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.file import File
from ..models.group_member import GroupMember
from ..models.notification import NotificationCategory
from ..models.public_link import PublicLink
from ..models.public_link_attempt import (
    PublicLinkAttempt,
    PublicLinkAttemptOutcome,
)
from ..models.share import Share, ShareState
from ..models.user import User, UserRole
from ..utils.crypto import (
    argon2_hash,
    argon2_verify,
    encrypt_setting,
    random_token,
    sha256_hex,
)
from . import notification as notif_svc
from . import settings_registry
from . import site as site_svc
from .audit import record_audit_event

logger = logging.getLogger("fileheron.public_link")


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


class CreatedLink(NamedTuple):
    record: PublicLink
    plaintext_token: str  # shown to creator once


def create_link(
    db: Session,
    *,
    actor: User,
    share: Share,
    password: str | None,
    download_limit: int | None,
    notify_on_download: bool,
    request=None,
) -> CreatedLink:
    """Create the (single) public link for a share. Refuses if the share
    is not in `active` state or already has a link.

    Caller commits."""
    if not is_allowed_to_create(db, actor):
        raise AppError(
            403,
            "PUBLIC_LINK_NOT_ALLOWED",
            "Your administrator has restricted public-link creation.",
        )
    if share.state != ShareState.active:
        raise AppError(
            409, "SHARE_NOT_ACTIVE", "Cannot create a public link for a non-active share."
        )
    if share.created_by_id != actor.id and actor.role != UserRole.admin:
        raise AppError(403, "FORBIDDEN", "Only the share owner or an admin can do that.")

    existing = (
        db.query(PublicLink)
        .filter(PublicLink.share_id == share.id, PublicLink.revoked_at.is_(None))
        .one_or_none()
    )
    if existing is not None:
        raise AppError(
            409,
            "PUBLIC_LINK_EXISTS",
            "A public link already exists for this share. Revoke it first.",
        )
    if download_limit is not None and download_limit <= 0:
        raise AppError(
            400, "INVALID_DOWNLOAD_LIMIT", "download_limit must be a positive integer."
        )

    plaintext = random_token(32)
    record = PublicLink(
        share_id=share.id,
        token_hash=sha256_hex(plaintext),
        # Fernet ciphertext lets the owner re-view the URL on share
        # detail; same crypto pattern as OIDC client_secret + TOTP.
        token_encrypted=encrypt_setting(plaintext),
        password_hash=argon2_hash(password) if password else None,
        download_limit=download_limit,
        downloads_remaining=download_limit,
        notify_on_download=notify_on_download,
        created_by_id=actor.id,
    )
    db.add(record)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.public_link_created,
        actor_user_id=actor.id,
        target_type="public_link",
        target_id=record.id,
        metadata={
            "share_id": share.id,
            "has_password": password is not None,
            "download_limit": download_limit,
            "notify_on_download": notify_on_download,
        },
        request=request,
    )
    return CreatedLink(record=record, plaintext_token=plaintext)


def get_active_link_for_share(db: Session, share_id: str) -> PublicLink | None:
    return (
        db.query(PublicLink)
        .filter(PublicLink.share_id == share_id, PublicLink.revoked_at.is_(None))
        .one_or_none()
    )


def get_link_by_token(db: Session, token: str) -> PublicLink:
    """Look up by hashed token. Raises 404 PUBLIC_LINK_NOT_FOUND."""
    record = (
        db.query(PublicLink)
        .filter(PublicLink.token_hash == sha256_hex(token))
        .one_or_none()
    )
    if record is None:
        raise AppError(404, "PUBLIC_LINK_NOT_FOUND", "Public link not found.")
    return record


def assert_link_usable(db: Session, link: PublicLink) -> None:
    """Validate link state. Raises if revoked, locked-out, expired share,
    counter exhausted, or share revoked/expired/deleted."""
    if link.revoked_at is not None:
        raise AppError(410, "PUBLIC_LINK_REVOKED", "This public link has been revoked.")
    if link.locked_until is not None and link.locked_until > _utcnow():
        raise AppError(
            423,
            "PUBLIC_LINK_LOCKED",
            "This public link is temporarily locked due to repeated failed unlocks.",
        )
    if link.downloads_remaining is not None and link.downloads_remaining <= 0:
        raise AppError(
            410, "PUBLIC_LINK_EXHAUSTED", "This public link's download limit has been reached."
        )
    share = db.query(Share).filter(Share.id == link.share_id).one_or_none()
    if share is None:
        raise AppError(404, "SHARE_NOT_FOUND", "Share for this public link is missing.")
    if share.state != ShareState.active:
        raise AppError(410, "SHARE_NOT_ACTIVE", "The underlying share is no longer active.")
    # NULL expires_at = never-expire (v1.1.4) — skip the time check.
    if share.expires_at is not None and share.expires_at < _utcnow():
        raise AppError(410, "SHARE_EXPIRED", "The underlying share has expired.")


def _record_attempt(
    db: Session,
    *,
    link: PublicLink,
    ip: str | None,
    outcome: PublicLinkAttemptOutcome,
) -> None:
    db.add(
        PublicLinkAttempt(
            public_link_id=link.id, ip=ip, outcome=outcome
        )
    )
    db.flush()


# A single IP that fails repeatedly throttles ITSELF (the router returns
# 429) but must NOT be able to set the link-wide lock — otherwise anyone
# holding the URL can DoS the legitimate recipients with ~10 bad guesses
# (audit finding M5). The link-wide lock only escalates when failures span
# several distinct IPs — the genuine distributed-brute-force signal.
MIN_DISTINCT_IPS_FOR_LOCK = 3


def _recent_failure_count(db: Session, link: PublicLink) -> int:
    window_sec = settings_registry.effective(
        db, settings_registry.K.PUBLIC_LINK_PASSWORD_WINDOW_SEC
    )
    cutoff = _utcnow() - timedelta(seconds=window_sec)
    return (
        db.query(PublicLinkAttempt)
        .filter(
            PublicLinkAttempt.public_link_id == link.id,
            PublicLinkAttempt.outcome == PublicLinkAttemptOutcome.failure,
            PublicLinkAttempt.attempted_at >= cutoff,
        )
        .count()
    )


def recent_ip_failure_count(db: Session, link: PublicLink, ip: str | None) -> int:
    if ip is None:
        return 0
    window_sec = settings_registry.effective(
        db, settings_registry.K.PUBLIC_LINK_PASSWORD_WINDOW_SEC
    )
    cutoff = _utcnow() - timedelta(seconds=window_sec)
    return (
        db.query(PublicLinkAttempt)
        .filter(
            PublicLinkAttempt.public_link_id == link.id,
            PublicLinkAttempt.ip == ip,
            PublicLinkAttempt.outcome == PublicLinkAttemptOutcome.failure,
            PublicLinkAttempt.attempted_at >= cutoff,
        )
        .count()
    )


def ip_is_rate_limited(db: Session, link: PublicLink, ip: str | None) -> bool:
    """True when THIS ip has hit the per-IP failure cap in the window.
    The unlock router refuses it (429) without locking out other IPs."""
    return (
        recent_ip_failure_count(db, link, ip)
        >= settings_registry.effective(db, settings_registry.K.PUBLIC_LINK_PASSWORD_RATE_LIMIT)
    )


def _recent_distinct_failure_ips(db: Session, link: PublicLink) -> int:
    window_sec = settings_registry.effective(
        db, settings_registry.K.PUBLIC_LINK_PASSWORD_WINDOW_SEC
    )
    cutoff = _utcnow() - timedelta(seconds=window_sec)
    return (
        db.query(PublicLinkAttempt.ip)
        .filter(
            PublicLinkAttempt.public_link_id == link.id,
            PublicLinkAttempt.outcome == PublicLinkAttemptOutcome.failure,
            PublicLinkAttempt.attempted_at >= cutoff,
        )
        .distinct()
        .count()
    )


def verify_password(
    db: Session,
    *,
    link: PublicLink,
    password: str,
    ip: str | None,
) -> bool:
    """Returns True on match. On miss, records the attempt and may set
    `locked_until` if the failure count crosses the threshold."""
    if link.password_hash is None:
        # No password set — treat any unlock attempt as immediate success.
        # (Caller shouldn't be asking, but be permissive.)
        return True

    if hmac.compare_digest("", password):
        # Empty password → fast fail.
        _record_attempt(db, link=link, ip=ip, outcome=PublicLinkAttemptOutcome.failure)
        return False

    ok = argon2_verify(link.password_hash, password)
    outcome = (
        PublicLinkAttemptOutcome.success if ok else PublicLinkAttemptOutcome.failure
    )
    _record_attempt(db, link=link, ip=ip, outcome=outcome)
    if ok:
        return True

    failures = _recent_failure_count(db, link)
    distinct_ips = _recent_distinct_failure_ips(db, link)
    # Link-wide lock ONLY for a distributed attack (many IPs). A single
    # noisy IP is handled per-IP by the router's rate-limit check.
    if (
        failures >= settings_registry.effective(db, settings_registry.K.PUBLIC_LINK_PASSWORD_RATE_LIMIT)
        and distinct_ips >= MIN_DISTINCT_IPS_FOR_LOCK
    ):
        link.locked_until = _utcnow() + timedelta(
            seconds=settings_registry.effective(db, settings_registry.K.PUBLIC_LINK_LOCKOUT_SEC)
        )
        _record_attempt(db, link=link, ip=ip, outcome=PublicLinkAttemptOutcome.locked)
        logger.warning(
            "public_link %s locked: %d failures from %d distinct IPs in %ds window",
            link.id,
            failures,
            distinct_ips,
            settings_registry.effective(db, settings_registry.K.PUBLIC_LINK_PASSWORD_WINDOW_SEC),
        )
    return False


def decrement_counter(
    db: Session, *, link: PublicLink
) -> tuple[bool, int | None]:
    """Atomically decrement downloads_remaining iff > 0.

    Returns ``(allowed, new_remaining)``:
    - ``allowed`` is True when the download is permitted.
    - ``new_remaining`` is the post-decrement value, or ``None`` for
      unlimited links. The session-level ``link`` is refreshed on a
      successful decrement so callers reading ``link.downloads_remaining``
      see the post-update value (used by ``notify_owner_on_download`` to
      report the correct count to the share owner).
    """
    if link.downloads_remaining is None:
        return True, None
    stmt = (
        update(PublicLink)
        .where(
            PublicLink.id == link.id,
            PublicLink.downloads_remaining > 0,
        )
        .values(downloads_remaining=PublicLink.downloads_remaining - 1)
    )
    result = db.execute(stmt)
    db.flush()
    if result.rowcount == 0:
        return False, link.downloads_remaining
    db.refresh(link)
    return True, link.downloads_remaining


def revoke(
    db: Session, *, actor: User, link: PublicLink, request=None
) -> None:
    if link.revoked_at is not None:
        return  # idempotent
    if link.created_by_id != actor.id and actor.role != UserRole.admin:
        raise AppError(403, "FORBIDDEN", "Only the link owner or an admin can revoke.")
    link.revoked_at = _utcnow()
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.public_link_revoked,
        actor_user_id=actor.id,
        target_type="public_link",
        target_id=link.id,
        request=request,
    )


def record_consumption(
    db: Session, *, link: PublicLink, file_id: str, ip: str | None, request=None
) -> None:
    """Audit row for a successful download. Caller has already done the
    counter decrement and DownloadLog insert."""
    record_audit_event(
        db,
        event_type=AuditEventType.public_link_consumed,
        actor_user_id=None,
        target_type="public_link",
        target_id=link.id,
        metadata={"file_id": file_id, "ip": ip, "share_id": link.share_id},
        request=request,
    )


def notify_owner_on_download(
    db: Session,
    *,
    link: PublicLink,
    file: File,
    downloads_remaining: int | None,
) -> None:
    """Dispatch the public_link_downloaded notification to the link owner.

    No-op when the link's notify_on_download flag is unset, or the owner
    is missing/disabled. Caller passes the post-decrement counter so the
    payload reports the count the recipient actually has left, not the
    pre-decrement value off by one (the in-memory ``link`` is refreshed
    by ``decrement_counter`` on success, but routes that pass NULL-limit
    links never see a refresh — explicit param keeps both paths honest)."""
    if not link.notify_on_download:
        return
    owner = db.query(User).filter(User.id == link.created_by_id).one_or_none()
    if owner is None or owner.is_disabled:
        return
    share = db.query(Share).filter(Share.id == link.share_id).one()
    share_url = f"{site_svc.get_site_url(db)}/share/{share.id}"
    notif_svc.dispatch(
        db,
        user=owner,
        category=NotificationCategory.public_link_downloaded,
        payload={
            "owner_name": owner.display_name,
            "subject": share.subject,
            "filename": file.original_filename,
            "size_bytes": file.size_bytes,
            "at": _utcnow(),
            "downloads_remaining": downloads_remaining,
            "share_url": share_url,
        },
        link_url=share_url,
        email_to=owner.email,
    )


# ---------------------------------------------------------------------------
# Policy gate (post-Phase 10). Mirrors services/api_token.py exactly.
# ---------------------------------------------------------------------------

POLICY_MODES = ("everyone", "employees_admins", "admins_only", "disabled")
DEFAULT_POLICY_MODE = "everyone"


def _resolve_policy(db: Session) -> tuple[str, list[int], list[int]]:
    """Read policy from app_settings. Returns (mode, allowed_user_ids,
    allowed_group_ids). Falls back to defaults so an unconfigured deploy
    keeps working."""
    from . import settings as settings_svc

    mode = (
        settings_svc.get(db, settings_svc.Keys.PUBLIC_LINK_POLICY_MODE)
        or DEFAULT_POLICY_MODE
    )
    if mode not in POLICY_MODES:
        mode = DEFAULT_POLICY_MODE

    raw_users = (
        settings_svc.get(db, settings_svc.Keys.PUBLIC_LINK_ALLOWED_USERS) or "[]"
    )
    raw_groups = (
        settings_svc.get(db, settings_svc.Keys.PUBLIC_LINK_ALLOWED_GROUPS) or "[]"
    )
    import json

    try:
        user_ids = [int(x) for x in json.loads(raw_users)]
    except (ValueError, TypeError):
        user_ids = []
    try:
        group_ids = [int(x) for x in json.loads(raw_groups)]
    except (ValueError, TypeError):
        group_ids = []
    return mode, user_ids, group_ids


def is_allowed_to_create(db: Session, user: User) -> bool:
    """True if `user` may create a public link under the active policy.
    Admin always passes (operator escape hatch)."""
    if user.role == UserRole.admin:
        return True
    mode, allowed_users, allowed_groups = _resolve_policy(db)
    if mode == "everyone":
        return True
    if mode == "employees_admins" and user.role == UserRole.employee:
        return True
    if user.id in allowed_users:
        return True
    if allowed_groups:
        hit = (
            db.query(GroupMember.user_id)
            .filter(
                GroupMember.user_id == user.id,
                GroupMember.group_id.in_(allowed_groups),
            )
            .first()
        )
        if hit is not None:
            return True
    return False
