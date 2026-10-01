"""Reading a secret (v2.24.0): who may, how a view counts, what a wrong
passphrase does.

The order of a reveal is the point of this module, so it is spelled out once:

    lock the secret row  ->  is it still readable (expiry checked HERE, never
    left to the sweep)  ->  is this principal eligible, not burned, views left
    ->  throttle  ->  open the passphrase layer  ->  claim a view  ->  decrypt
    ->  record the view, audit, tell the sender  ->  burn if nobody is left
    ->  commit  ->  answer (no-store).

- A view is claimed by ONE conditional UPDATE (`... WHERE views_used < X`) and
  its rowcount - the public-link counter pattern - against the counter the
  scope names: the secret's own (`total`), the person's `secret_user_states`
  row (`per_person`), or the recipient entry's pool (`per_recipient`; a group's
  members share it). Addresses and the link always count on their own row. The
  secret row is also locked FOR UPDATE for the whole reveal.
- A wrong passphrase never costs a view: the passphrase is checked BEFORE the
  claim. It is answered 403, never 401 - a 401 on a signed-in route sends the
  SPA into its refresh-and-replay path and signs the user out for a typo. The
  attempt is COMMITTED before the error is raised, or the AppError's rollback
  would erase the evidence the throttle counts.
- Throttle + lock (the default) mirrors public links: a source over the limit
  in the window is refused (429), and an address/link is locked for everyone
  only once the failures come from several addresses - otherwise one holder of
  the URL could lock out the real recipient with a few guesses. Burn mode ends
  the secret for that one principal after N wrong passphrases instead.
- Nothing here runs on a GET. Mail gateways fetch links before people do; the
  anonymous routes take the token in a POST body and only an explicit reveal
  costs a view.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, NoReturn

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.secret import (
    Secret,
    SecretAccessEvent,
    SecretAccessOutcome,
    SecretRecipient,
    SecretState,
    SecretUserState,
    SecretViewScope,
)
from ..models.user import User, UserRole
from ..utils.columns import declared_width
from ..utils.crypto import (
    SecretPassphraseError,
    SecretUndecryptableError,
    open_secret_content,
    unwrap_secret_key,
)
from ..utils.dbresult import updated_rows
from ..utils.timeutil import utc_now
from . import secret as secret_svc
from .audit import record_audit_event

logger = logging.getLogger("fileheron.secret_reveal")

_IP_MAX = declared_width(SecretAccessEvent.__table__.c.ip)

# A link/address is locked for everyone only when the failures come from at
# least this many addresses - the public-link rule (MIN_DISTINCT_IPS_FOR_LOCK).
MIN_DISTINCT_IPS_FOR_LOCK = 3


@dataclass
class RevealResult:
    content: str
    views_left: int | None
    ended: bool


@dataclass
class PeekResult:
    requires_passphrase: bool
    label: str | None
    sender_name: str | None
    expires_at: datetime | None
    views_left: int | None
    locked_until: datetime | None
    attempts_left: int | None


def _clip_ip(ip: str | None) -> str | None:
    return ip[:_IP_MAX] if ip else None


def _lock(db: Session, secret_id: str) -> Secret | None:
    return (
        db.query(Secret)
        .filter(Secret.id == secret_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )


def assert_readable(db: Session, secret: Secret) -> None:
    """410 SECRET_ENDED unless the secret is active - ending it first when it
    has expired and the sweep has not got to it yet, so expiry never depends on
    the cron."""
    if (
        secret.state == SecretState.active
        and secret.expires_at is not None
        and secret.expires_at <= utc_now()
    ):
        secret_svc.end_secret(db, secret, SecretState.expired, reason="expired")
        db.commit()
    if secret.state != SecretState.active:
        raise AppError(
            410,
            "SECRET_ENDED",
            "This secret is no longer available.",
            details={"reason": secret.state.value},
        )


def _effective_state(secret: Secret) -> SecretState:
    """The state a read-only path reports: an active secret past its expiry is
    expired, whether or not anything has written that down yet."""
    if (
        secret.state == SecretState.active
        and secret.expires_at is not None
        and secret.expires_at <= utc_now()
    ):
        return SecretState.expired
    return secret.state


def _event(
    db: Session,
    secret: Secret,
    outcome: SecretAccessOutcome,
    *,
    rec: SecretRecipient | None = None,
    user_id: int | None = None,
    ip: str | None = None,
) -> None:
    db.add(
        SecretAccessEvent(
            secret_id=secret.id,
            recipient_id=rec.id if rec is not None else None,
            user_id=user_id,
            ip=_clip_ip(ip),
            outcome=outcome,
        )
    )
    db.flush()


def _failures_since(db: Session, secret: Secret, since: datetime, **filters) -> int:
    q = db.query(func.count(SecretAccessEvent.id)).filter(
        SecretAccessEvent.secret_id == secret.id,
        SecretAccessEvent.outcome == SecretAccessOutcome.wrong_passphrase,
        SecretAccessEvent.created_at >= since,
    )
    for column, value in filters.items():
        attr = getattr(SecretAccessEvent, column)
        q = q.filter(attr.is_(None) if value is None else attr == value)
    return int(q.scalar() or 0)


def _distinct_failure_ips(db: Session, rec: SecretRecipient, since: datetime) -> int:
    return int(
        db.query(func.count(func.distinct(SecretAccessEvent.ip)))
        .filter(
            SecretAccessEvent.recipient_id == rec.id,
            SecretAccessEvent.outcome == SecretAccessOutcome.wrong_passphrase,
            SecretAccessEvent.created_at >= since,
        )
        .scalar()
        or 0
    )


def _throttled() -> AppError:
    return AppError(
        429,
        "SECRET_RATE_LIMITED",
        "Too many wrong passphrases. Try again later.",
    )


def _wrong_passphrase(burn_after: int | None, failed: int) -> AppError:
    return AppError(
        403,
        "SECRET_PASSPHRASE_INVALID",
        "That passphrase is not correct.",
        details={"attempts_left": max(0, burn_after - failed) if burn_after else None},
    )


def _burned_for_you() -> AppError:
    return AppError(
        410,
        "SECRET_RECIPIENT_BURNED",
        "Too many wrong passphrases: this secret is gone for you.",
    )


def _exhausted() -> AppError:
    return AppError(
        410, "SECRET_VIEWS_EXHAUSTED", "You have used up the views of this secret."
    )


def _incr(model: Any, where: tuple[Any, ...], **cols: Any) -> Any:
    """`UPDATE model SET col = col + 1 WHERE ...` for every column passed as
    True; any other value is assigned as given."""
    values = {name: getattr(model, name) + 1 for name, v in cols.items() if v is True}
    values.update({name: v for name, v in cols.items() if v is not True})
    return update(model).where(*where).values(**values).execution_options(
        synchronize_session=False
    )


def _claimed(db: Session, stmt: Any) -> bool:
    return updated_rows(db.execute(stmt)) == 1


def _unwrap(db: Session, secret: Secret, passphrase: str | None) -> bytes:
    if not secret.key_encrypted or not secret.ciphertext:
        # Ended between the state check and here - cannot happen under the row
        # lock, but an AppError beats a decrypt of None.
        raise AppError(410, "SECRET_ENDED", "This secret is no longer available.")
    return unwrap_secret_key(
        secret.key_encrypted,
        kdf_salt=secret.kdf_salt,
        kdf_params=secret.kdf_params,
        passphrase=passphrase,
    )


def _unreadable(secret: Secret) -> AppError:
    logger.error(
        "secret %s cannot be decrypted under the current JWT_SECRET "
        "(rotated without backend/scripts/rotate_jwt_secret.py?)",
        secret.id,
    )
    return AppError(
        500,
        "SECRET_UNREADABLE",
        "This secret cannot be decrypted on this server. Ask the sender to send it again.",
    )


# ---------------------------------------------------------------------------
# Account recipients
# ---------------------------------------------------------------------------


@dataclass
class UserStanding:
    """What the detail page needs to know about one reader."""

    state: SecretUserState | None
    reach: secret_svc.AccountReach
    views_left: int | None
    burned: bool

    @property
    def eligible(self) -> bool:
        return self.state is not None and self.reach.eligible


def standing(db: Session, secret: Secret, user: User) -> UserStanding:
    state = secret_svc.user_state(db, secret, user.id)
    reach = secret_svc.account_reach(db, secret, user.id)
    left = secret_svc.views_left_for_user(secret, state, reach) if state is not None else 0
    return UserStanding(
        state=state,
        reach=reach,
        views_left=left,
        burned=state is not None and state.burned_at is not None,
    )


def can_reveal(secret: Secret, st: UserStanding) -> bool:
    return (
        _effective_state(secret) == SecretState.active
        and st.eligible
        and not st.burned
        and (st.views_left is None or st.views_left > 0)
    )


def _claim_for_user(
    db: Session, secret: Secret, state: SecretUserState, reach: secret_svc.AccountReach
) -> SecretRecipient | None:
    """Claim one view for an account person. Returns the recipient row it was
    counted against (for the event), or None when nothing was left."""
    x = secret.max_views
    now = utc_now()
    via = reach.via
    s_where = (Secret.id == secret.id,)
    u_where = (
        SecretUserState.secret_id == secret.id,
        SecretUserState.user_id == state.user_id,
        SecretUserState.burned_at.is_(None),
    )
    if x is None:
        db.execute(_incr(Secret, s_where, views_used=True))
        db.execute(_incr(SecretUserState, u_where, views_used=True, last_viewed_at=now))
        return via
    if secret.view_scope == SecretViewScope.total:
        if not _claimed(db, _incr(Secret, (*s_where, Secret.views_used < x), views_used=True)):
            return None
        db.execute(_incr(SecretUserState, u_where, views_used=True, last_viewed_at=now))
        return via
    if secret.view_scope == SecretViewScope.per_person:
        if not _claimed(
            db,
            _incr(
                SecretUserState,
                (*u_where, SecretUserState.views_used < x),
                views_used=True,
                last_viewed_at=now,
            ),
        ):
            return None
        db.execute(_incr(Secret, s_where, views_used=True))
        return via
    # per_recipient: their own entry when addressed directly - a direct
    # recipient never draws on a group's pool - otherwise the first of their
    # groups with views left.
    pools = [reach.direct] if reach.direct is not None else list(reach.groups)
    for pool in pools:
        if _claimed(
            db,
            _incr(
                SecretRecipient,
                (SecretRecipient.id == pool.id, SecretRecipient.views_used < x),
                views_used=True,
            ),
        ):
            db.execute(_incr(Secret, s_where, views_used=True))
            db.execute(_incr(SecretUserState, u_where, views_used=True, last_viewed_at=now))
            return pool
    return None


def reveal_for_user(
    db: Session,
    *,
    secret_id: str,
    user: User,
    passphrase: str | None,
    ip: str | None,
    request=None,
) -> RevealResult:
    secret = _lock(db, secret_id)
    if secret is None:
        raise AppError(404, "SECRET_NOT_FOUND", "This secret does not exist.")
    state = secret_svc.user_state(db, secret, user.id)
    if state is None:
        if user.id == secret.created_by_id or user.role == UserRole.admin:
            # They may see that it exists, never what it says.
            raise AppError(
                403,
                "SECRET_REVEAL_FORBIDDEN",
                "Only the recipients of a secret can open it.",
            )
        raise AppError(404, "SECRET_NOT_FOUND", "This secret does not exist.")
    assert_readable(db, secret)
    reach = secret_svc.account_reach(db, secret, user.id)
    if not reach.eligible:
        raise AppError(
            403,
            "SECRET_NOT_A_RECIPIENT",
            "You are no longer a recipient of this secret.",
        )
    if state.burned_at is not None:
        raise _burned_for_you()
    if secret_svc.views_left_for_user(secret, state, reach) == 0:
        raise _exhausted()

    lim = secret_svc.limits(db)
    if secret.has_passphrase:
        if not passphrase:
            raise AppError(
                400, "SECRET_PASSPHRASE_REQUIRED", "This secret needs its passphrase."
            )
        since = utc_now() - timedelta(seconds=lim.passphrase_window_sec)
        if _failures_since(db, secret, since, user_id=user.id) >= lim.passphrase_rate_limit:
            raise _throttled()
    try:
        data_key = _unwrap(db, secret, passphrase)
    except SecretPassphraseError:
        _user_wrong_passphrase(db, secret, state, reach, ip=ip, request=request)
    except SecretUndecryptableError:
        raise _unreadable(secret) from None

    via = _claim_for_user(db, secret, state, reach)
    if via is None:
        raise _exhausted()
    try:
        content = open_secret_content(secret.ciphertext or "", data_key)
    except SecretUndecryptableError:
        raise _unreadable(secret) from None

    _event(db, secret, SecretAccessOutcome.viewed, rec=via, user_id=user.id, ip=ip)
    record_audit_event(
        db,
        event_type=AuditEventType.secret_viewed,
        actor_user_id=user.id,
        target_type="secret",
        target_id=secret.id,
        metadata={"via": via.kind.value, "recipient_id": via.id},
        request=request,
    )
    db.refresh(secret)
    db.refresh(state)
    for rec in (reach.direct, *reach.groups):
        if rec is not None:
            db.refresh(rec)
    left = secret_svc.views_left_for_user(secret, state, reach)
    secret_svc.notify_viewed(db, secret, viewer=user, rec=None, ip=None, views_left=left)
    ended = secret_svc.end_if_exhausted(db, secret)
    db.commit()
    return RevealResult(content=content, views_left=left, ended=ended)


def _user_wrong_passphrase(
    db: Session,
    secret: Secret,
    state: SecretUserState,
    reach: secret_svc.AccountReach,
    *,
    ip: str | None,
    request: Any,
) -> NoReturn:
    """Record a wrong passphrase from a signed-in recipient, commit, raise."""
    _event(
        db,
        secret,
        SecretAccessOutcome.wrong_passphrase,
        rec=reach.via,
        user_id=state.user_id,
        ip=ip,
    )
    db.execute(
        _incr(
            SecretUserState,
            (
                SecretUserState.secret_id == secret.id,
                SecretUserState.user_id == state.user_id,
            ),
            failed_attempts=True,
        )
    )
    db.refresh(state)
    burn_after = secret.burn_after_failures
    if burn_after and state.failed_attempts >= burn_after:
        state.burned_at = utc_now()
        _event(db, secret, SecretAccessOutcome.burned, rec=reach.via, user_id=state.user_id, ip=ip)
        record_audit_event(
            db,
            event_type=AuditEventType.secret_recipient_burned,
            actor_user_id=state.user_id,
            target_type="secret",
            target_id=secret.id,
            metadata={"user_id": state.user_id, "failed_attempts": state.failed_attempts},
            request=request,
        )
        secret_svc.end_if_exhausted(db, secret)
        db.commit()
        raise _burned_for_you()
    db.commit()
    raise _wrong_passphrase(burn_after, state.failed_attempts)


# ---------------------------------------------------------------------------
# Addresses and the link (anonymous, token in the request BODY)
# ---------------------------------------------------------------------------


def peek(db: Session, token: str) -> PeekResult:
    """What the reveal page shows before anything is revealed. Costs nothing and
    writes nothing. While a passphrase is set the label and sender stay hidden:
    the link alone must not say what it protects (the public-link landing rule)."""
    rec = secret_svc.recipient_by_token(db, token)
    secret = rec.secret
    state = _effective_state(secret)
    if state != SecretState.active:
        raise AppError(
            410,
            "SECRET_ENDED",
            "This secret is no longer available.",
            details={"reason": state.value},
        )
    _assert_principal_live(rec)
    left = secret_svc.views_left_for_recipient(secret, rec)
    if left == 0:
        raise _exhausted()
    gated = secret.has_passphrase
    now = utc_now()
    sender = secret.created_by
    burn_after = secret.burn_after_failures
    return PeekResult(
        requires_passphrase=gated,
        label=None if gated else secret.label,
        sender_name=None if gated or sender is None else sender.display_name,
        expires_at=secret.expires_at,
        views_left=left,
        locked_until=rec.locked_until if rec.locked_until and rec.locked_until > now else None,
        attempts_left=max(0, burn_after - rec.failed_attempts) if burn_after and gated else None,
    )


def _assert_principal_live(rec: SecretRecipient) -> None:
    if rec.revoked_at is not None:
        raise AppError(
            410, "SECRET_LINK_REVOKED", "The sender replaced or removed this link."
        )
    if rec.burned_at is not None:
        raise _burned_for_you()


def _claim_for_recipient(db: Session, secret: Secret, rec: SecretRecipient) -> bool:
    x = secret.max_views
    s_where = (Secret.id == secret.id,)
    r_where = (
        SecretRecipient.id == rec.id,
        SecretRecipient.burned_at.is_(None),
        SecretRecipient.revoked_at.is_(None),
    )
    if x is None:
        db.execute(_incr(Secret, s_where, views_used=True))
        db.execute(_incr(SecretRecipient, r_where, views_used=True))
        return True
    if secret.view_scope == SecretViewScope.total:
        if not _claimed(db, _incr(Secret, (*s_where, Secret.views_used < x), views_used=True)):
            return False
        db.execute(_incr(SecretRecipient, r_where, views_used=True))
        return True
    # per_person and per_recipient are the same thing for an address or the
    # link: it is one principal and one entry.
    if not _claimed(
        db, _incr(SecretRecipient, (*r_where, SecretRecipient.views_used < x), views_used=True)
    ):
        return False
    db.execute(_incr(Secret, s_where, views_used=True))
    return True


def reveal_by_token(
    db: Session,
    *,
    token: str,
    passphrase: str | None,
    ip: str | None,
    request=None,
) -> RevealResult:
    rec = secret_svc.recipient_by_token(db, token)
    secret = _lock(db, rec.secret_id)
    if secret is None:
        raise AppError(404, "SECRET_NOT_FOUND", "This secret does not exist.")
    db.refresh(rec)
    assert_readable(db, secret)
    _assert_principal_live(rec)
    now = utc_now()
    if rec.locked_until is not None and rec.locked_until > now:
        raise AppError(
            423,
            "SECRET_LOCKED",
            "This secret is locked for a while after too many wrong passphrases.",
            details={"locked_until": rec.locked_until.isoformat()},
        )
    if secret_svc.views_left_for_recipient(secret, rec) == 0:
        raise _exhausted()

    lim = secret_svc.limits(db)
    if secret.has_passphrase:
        if not passphrase:
            raise AppError(
                400, "SECRET_PASSPHRASE_REQUIRED", "This secret needs its passphrase."
            )
        since = now - timedelta(seconds=lim.passphrase_window_sec)
        if (
            _failures_since(db, secret, since, recipient_id=rec.id, ip=_clip_ip(ip))
            >= lim.passphrase_rate_limit
        ):
            raise _throttled()
    try:
        data_key = _unwrap(db, secret, passphrase)
    except SecretPassphraseError:
        _token_wrong_passphrase(db, secret, rec, lim, ip=ip, request=request)
    except SecretUndecryptableError:
        raise _unreadable(secret) from None

    if not _claim_for_recipient(db, secret, rec):
        raise _exhausted()
    try:
        content = open_secret_content(secret.ciphertext or "", data_key)
    except SecretUndecryptableError:
        raise _unreadable(secret) from None

    _event(db, secret, SecretAccessOutcome.viewed, rec=rec, ip=ip)
    record_audit_event(
        db,
        event_type=AuditEventType.secret_viewed,
        actor_user_id=None,
        target_type="secret",
        target_id=secret.id,
        metadata={"via": rec.kind.value, "recipient_id": rec.id},
        request=request,
    )
    db.refresh(secret)
    db.refresh(rec)
    left = secret_svc.views_left_for_recipient(secret, rec)
    secret_svc.notify_viewed(db, secret, viewer=None, rec=rec, ip=ip, views_left=left)
    ended = secret_svc.end_if_exhausted(db, secret)
    db.commit()
    return RevealResult(content=content, views_left=left, ended=ended)


def _token_wrong_passphrase(
    db: Session,
    secret: Secret,
    rec: SecretRecipient,
    lim: secret_svc.Limits,
    *,
    ip: str | None,
    request: Any,
) -> NoReturn:
    """Record a wrong passphrase through an address/link, commit, raise."""
    _event(db, secret, SecretAccessOutcome.wrong_passphrase, rec=rec, ip=ip)
    db.execute(_incr(SecretRecipient, (SecretRecipient.id == rec.id,), failed_attempts=True))
    db.refresh(rec)
    burn_after = secret.burn_after_failures
    if burn_after and rec.failed_attempts >= burn_after:
        rec.burned_at = utc_now()
        rec.token_encrypted = None
        _event(db, secret, SecretAccessOutcome.burned, rec=rec, ip=ip)
        record_audit_event(
            db,
            event_type=AuditEventType.secret_recipient_burned,
            actor_user_id=None,
            target_type="secret",
            target_id=secret.id,
            metadata={"recipient_id": rec.id, "failed_attempts": rec.failed_attempts},
            request=request,
        )
        secret_svc.end_if_exhausted(db, secret)
        db.commit()
        raise _burned_for_you()
    if not burn_after:
        # Throttle + lock: lock this address/link for everyone only when the
        # failures in the window come from several sources. One source is
        # stopped by the per-source throttle above and cannot lock anyone out.
        since = utc_now() - timedelta(seconds=lim.passphrase_window_sec)
        failures = _failures_since(db, secret, since, recipient_id=rec.id)
        if (
            failures >= lim.passphrase_rate_limit
            and _distinct_failure_ips(db, rec, since) >= MIN_DISTINCT_IPS_FOR_LOCK
        ):
            rec.locked_until = utc_now() + timedelta(seconds=lim.passphrase_lockout_sec)
            _event(db, secret, SecretAccessOutcome.locked, rec=rec, ip=ip)
            logger.warning(
                "secret %s recipient %s locked: %d wrong passphrases in %ds",
                secret.id,
                rec.id,
                failures,
                lim.passphrase_window_sec,
            )
    db.commit()
    raise _wrong_passphrase(burn_after, rec.failed_attempts)
