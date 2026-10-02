"""Secret requests (v2.24.0): asking someone for a password or other short secret.

The requester names who may answer, says what they want, how long the request
stays open and how the answer may be read. The FIRST answer fulfils it and
becomes an ordinary secret (`services/secret.py::store_sealed`, `is_answer`)
whose only reader is the requester - so reveal, counting, the passphrase
throttle, shredding and expiry are the secrets code, unchanged.

Rules that hold here:

- Who may ask, and whom, is exactly who may SEND a secret to them
  (`secret.may_send`, `secret.may_send_external`, `secret._validate_recipients`):
  a client asks only connected employees, and never an address or a link.
- The answer path locks the request row, checks it is open and not expired
  ITSELF (never leaving expiry to the sweep), and claims it with ONE conditional
  UPDATE on `state = 'open'`: two answers cannot both land.
- A group member may answer if they were a member when the request was made
  AND still are; the requester never answers their own request.
- The requester's passphrase is never stored: only the public half of a key
  pair derived from it (`utils/crypto.request_keypair`).
- Request tokens ride the URL FRAGMENT (`{site}/r#<token>`) and reach the API in
  a POST body, like secret links. Once a request is no longer open every token's
  encrypted copy is NULLed; the hash stays, so an old link answers "closed".
- Label and note are shown to everyone who may answer - link holders included.
  Audit rows carry counts, never addresses or tokens.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session

from ..config import settings
from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.group_member import GroupMember
from ..models.notification import NotificationCategory
from ..models.secret import (
    Secret,
    SecretRecipient,
    SecretRecipientKind,
    SecretUserState,
    SecretViewScope,
)
from ..models.secret_request import (
    SecretRequest,
    SecretRequestGroupMember,
    SecretRequestState,
    SecretRequestTarget,
)
from ..models.user import User, UserRole
from ..utils.columns import declared_width
from ..utils.crypto import (
    SecretUndecryptableError,
    decrypt_setting,
    encrypt_setting,
    random_token,
    request_keypair,
    sha256_hex,
)
from ..utils.dbresult import updated_rows
from ..utils.like import LIKE_ESCAPE, contains
from ..utils.timeutil import utc_now
from . import secret as secret_svc
from .audit import record_audit_event

logger = logging.getLogger("fileheron.secret_request")

_OPEN = SecretRequestState.open
_LABEL_MAX = declared_width(SecretRequest.__table__.c.label)
_NOTE_MAX = declared_width(SecretRequest.__table__.c.note)
# The shortest lifetime an answer may be given (a minute: anything shorter is
# gone before the requester can open the notification).
MIN_ANSWER_LIFETIME_SEC = 60
# The mail to an address with no account, and its mail-log category - listed in
# `mail_log._AUTH_LINK_CATEGORIES`, so the log never offers to resend a live link.
EXTERNAL_TEMPLATE_SLUG = "secret_request_external"
EXTERNAL_MAIL_CATEGORY = EXTERNAL_TEMPLATE_SLUG
_ANONYMOUS_KINDS = (SecretRecipientKind.email, SecretRecipientKind.link)

_END_EVENTS = {
    SecretRequestState.cancelled: AuditEventType.secret_request_cancelled,
    SecretRequestState.expired: AuditEventType.secret_request_expired,
}


# ---------------------------------------------------------------------------
# URLs and tokens
# ---------------------------------------------------------------------------


def request_url(db: Session, token: str) -> str:
    """The answer page for a request token - the ONE builder (the create
    response, the requester's copy and the mail). The token is in the FRAGMENT."""
    from . import site as site_svc

    return f"{site_svc.get_site_url(db)}{settings.REQUEST_LINK_BASE_PATH}#{token}"


def detail_url(db: Session, req: SecretRequest) -> str:
    from . import site as site_svc

    return f"{site_svc.get_site_url(db)}/secrets/requests/{req.id}"


def stored_url(db: Session, target: SecretRequestTarget) -> str | None:
    """Rebuild a link from its encrypted token, or None (closed, or not
    decryptable after a JWT_SECRET rotation without the rotation script)."""
    if not target.token_encrypted:
        return None
    try:
        return request_url(db, decrypt_setting(target.token_encrypted))
    except SecretUndecryptableError:
        logger.warning("secret request target %s: link token undecryptable", target.id)
        return None


def _new_token_target(
    req: SecretRequest, kind: SecretRecipientKind, *, email: str | None = None
) -> tuple[SecretRequestTarget, str]:
    token = random_token(32)
    target = SecretRequestTarget(
        request_id=req.id,
        kind=kind,
        email=email,
        token_hash=sha256_hex(token),
        token_encrypted=encrypt_setting(token),
    )
    return target, token


def target_by_token(db: Session, token: str) -> SecretRequestTarget:
    target = (
        db.query(SecretRequestTarget)
        .filter(SecretRequestTarget.token_hash == sha256_hex(token))
        .one_or_none()
    )
    if target is None or target.kind not in _ANONYMOUS_KINDS:
        raise AppError(404, "SECRET_REQUEST_NOT_FOUND", "This request does not exist.")
    return target


# ---------------------------------------------------------------------------
# Who may answer
# ---------------------------------------------------------------------------


def answering_target(db: Session, req: SecretRequest, user: User) -> SecretRequestTarget | None:
    """The target row through which `user` may answer `req`: a direct target, or
    a group they were in when it was asked AND are in now. Never the requester,
    never a disabled account."""
    if user.id == req.requester_id or user.is_disabled:
        return None
    direct = (
        db.query(SecretRequestTarget)
        .filter(
            SecretRequestTarget.request_id == req.id,
            SecretRequestTarget.kind == SecretRecipientKind.user,
            SecretRequestTarget.target_user_id == user.id,
        )
        .one_or_none()
    )
    if direct is not None:
        return direct
    return (
        db.query(SecretRequestTarget)
        .join(SecretRequestGroupMember, SecretRequestGroupMember.target_id == SecretRequestTarget.id)
        .join(
            GroupMember,
            and_(
                GroupMember.group_id == SecretRequestTarget.target_group_id,
                GroupMember.user_id == SecretRequestGroupMember.user_id,
            ),
        )
        .filter(
            SecretRequestTarget.request_id == req.id,
            SecretRequestTarget.kind == SecretRecipientKind.group,
            SecretRequestGroupMember.user_id == user.id,
        )
        .order_by(SecretRequestTarget.id)
        .first()
    )


def viewer_role(db: Session, req: SecretRequest, user: User) -> str | None:
    """"requester" | "target" | "admin" | None. Anyone else gets a 404."""
    if user.id == req.requester_id:
        return "requester"
    if answering_target(db, req, user) is not None:
        return "target"
    if user.role == UserRole.admin:
        return "admin"
    return None


def get_or_404(db: Session, request_id: str) -> SecretRequest:
    req = db.get(SecretRequest, request_id)
    if req is None:
        raise AppError(404, "SECRET_REQUEST_NOT_FOUND", "This request does not exist.")
    return req


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------


@dataclass
class CreatedRequest:
    request: SecretRequest
    # Plaintext link token, when a link was created - for the response, once.
    link_token: str | None


def create_request(
    db: Session,
    *,
    requester: User,
    label: str,
    note: str | None,
    expires_at: datetime,
    answer_max_views: int | None,
    answer_expires_in_sec: int | None,
    passphrase: str | None,
    user_ids: list[int],
    group_ids: list[int],
    emails: list[str],
    create_link: bool,
    request=None,
) -> CreatedRequest:
    """Validate and store a request, tell its account targets and mail its
    addresses (after commit). Caller commits."""
    if not secret_svc.is_enabled(db):
        raise AppError(403, "SECRETS_DISABLED", "Secrets are turned off on this instance.")
    if not secret_svc.may_send(db, requester):
        raise AppError(
            403, "SECRET_NOT_ALLOWED", "Your administrator has restricted who may send secrets."
        )
    emails = list(dict.fromkeys(emails))
    if (emails or create_link) and not secret_svc.may_send_external(db, requester):
        raise AppError(
            403,
            "SECRET_EXTERNAL_NOT_ALLOWED",
            "You may not ask an email address or a link for a secret.",
        )
    label = (label or "").strip()
    if not label or len(label) > _LABEL_MAX:
        raise AppError(400, "SECRET_REQUEST_LABEL_INVALID", "Say what you are asking for.")
    note = (note or "").strip()[:_NOTE_MAX] or None

    lim = secret_svc.limits(db)
    now = utc_now()
    expires = secret_svc._normalise_expiry(expires_at)
    if expires is None or expires <= now:
        raise AppError(400, "EXPIRY_IN_PAST", "Expiry must be in the future.")
    if expires > now + timedelta(days=lim.max_expiry_days, minutes=1):
        raise AppError(
            400,
            "SECRET_LIMIT_EXCEEDED",
            f"A request may stay open at most {lim.max_expiry_days} days.",
            details={"field": "expires_at", "max": lim.max_expiry_days},
        )
    if answer_max_views is None and answer_expires_in_sec is None:
        raise AppError(400, "SECRET_NEEDS_LIMIT", "Set a view limit, a lifetime, or both.")
    if answer_max_views is not None and not 1 <= answer_max_views <= lim.max_views:
        raise AppError(
            400,
            "SECRET_LIMIT_EXCEEDED",
            f"A secret may allow at most {lim.max_views} views.",
            details={"field": "answer_max_views", "max": lim.max_views},
        )
    if answer_expires_in_sec is not None and not (
        MIN_ANSWER_LIFETIME_SEC <= answer_expires_in_sec <= lim.max_expiry_days * 86400
    ):
        raise AppError(
            400,
            "SECRET_LIMIT_EXCEEDED",
            f"An answer may live at most {lim.max_expiry_days} days.",
            details={"field": "answer_expires_in_sec", "max": lim.max_expiry_days},
        )

    user_ids = list(dict.fromkeys(user_ids))
    group_ids = list(dict.fromkeys(group_ids))
    if not user_ids and not group_ids and not emails and not create_link:
        raise AppError(
            400, "SECRET_NO_RECIPIENTS", "Ask at least one person, or create a link."
        )
    users, groups = secret_svc._validate_recipients(db, requester, user_ids, group_ids)

    keys = request_keypair(passphrase) if passphrase else None
    req = SecretRequest(
        requester_id=requester.id,
        label=label,
        note=note,
        state=_OPEN,
        expires_at=expires,
        answer_max_views=answer_max_views,
        answer_expires_in_sec=answer_expires_in_sec,
        kdf_salt=keys.kdf_salt if keys else None,
        kdf_params=keys.kdf_params if keys else None,
        public_key=keys.public_key if keys else None,
    )
    db.add(req)
    db.flush()

    asked: list[int] = []
    for u in users:
        db.add(
            SecretRequestTarget(
                request_id=req.id, kind=SecretRecipientKind.user, target_user_id=u.id
            )
        )
        asked.append(u.id)
    for g in groups:
        target = SecretRequestTarget(
            request_id=req.id, kind=SecretRecipientKind.group, target_group_id=g.id
        )
        db.add(target)
        db.flush()
        for uid in secret_svc._member_ids(db, g.id):
            if uid == requester.id:
                continue
            db.add(SecretRequestGroupMember(target_id=target.id, user_id=uid))
            asked.append(uid)
    mailed: list[tuple[SecretRequestTarget, str]] = []
    for email in emails:
        target, token = _new_token_target(req, SecretRecipientKind.email, email=email)
        db.add(target)
        mailed.append((target, token))
    link_token: str | None = None
    if create_link:
        target, link_token = _new_token_target(req, SecretRecipientKind.link)
        db.add(target)
    db.flush()

    asked_ids = list(dict.fromkeys(asked))
    record_audit_event(
        db,
        event_type=AuditEventType.secret_request_created,
        actor_user_id=requester.id,
        target_type="secret_request",
        target_id=req.id,
        metadata={
            "target_user_count": len(users),
            "target_group_count": len(groups),
            "asked_count": len(asked_ids),
            # A count, never the addresses (no account, out of erasure's reach).
            "email_count": len(emails),
            "has_link": create_link,
            "answer_max_views": answer_max_views,
            "answer_expires_in_sec": answer_expires_in_sec,
            "expires_at": expires.isoformat(),
            "has_passphrase": keys is not None,
        },
        request=request,
    )
    _notify_asked(db, req, requester, asked_ids)
    _mail_addresses(db, req, requester, mailed)
    return CreatedRequest(request=req, link_token=link_token)


def _notify_asked(db: Session, req: SecretRequest, requester: User, user_ids: list[int]) -> None:
    if not user_ids:
        return
    from . import notification as notif_svc

    url = detail_url(db, req)
    for u in db.query(User).filter(User.id.in_(user_ids)).order_by(User.id):
        notif_svc.dispatch(
            db,
            user=u,
            category=NotificationCategory.secret_requested,
            payload={
                "recipient_name": u.display_name,
                "requester_name": requester.display_name,
                "label": req.label,
                "note": req.note,
                "expires_at": req.expires_at,
                "request_url": url,
            },
            link_url=url,
            email_to=u.email,
        )


def _mail_addresses(
    db: Session,
    req: SecretRequest,
    requester: User,
    mailed: list[tuple[SecretRequestTarget, str]],
) -> None:
    """Mail each address its own answer link, in the requester's locale, with no
    unsubscribe footer (no account). Sent after the caller commits."""
    if not mailed:
        return
    from . import email as email_svc
    from . import mail_log
    from . import notification as notif_svc
    from . import site as site_svc

    now = utc_now()
    for target, token in mailed:
        payload = {
            "requester_name": requester.display_name,
            "label": req.label,
            "note": req.note,
            "link_url": request_url(db, token),
            "expires_at": req.expires_at,
        }
        try:
            subject, text, html = email_svc.render_email(
                requester.locale,
                EXTERNAL_TEMPLATE_SLUG,
                payload,
                app_url=site_svc.get_site_url(db),
                site_timezone=site_svc.get_site_timezone(db),
                app_name=site_svc.get_app_name(db),
                db=db,
            )
        except Exception:
            logger.exception(
                "secret request %s: rendering the mail for target %s failed", req.id, target.id
            )
            continue
        eid = mail_log.record_queued(
            db,
            recipient_email=target.email or "",
            recipient_user_id=None,
            category=EXTERNAL_MAIL_CATEGORY,
            template_slug=EXTERNAL_TEMPLATE_SLUG,
            subject=subject,
            text_body=text,
            html_body=html,
        )
        target.notified_at = now
        notif_svc._queue_email_job(
            db,
            {
                "to": target.email,
                "subject": subject,
                "text_body": text,
                "html_body": html,
                "email_log_id": eid,
            },
        )
    db.flush()


# ---------------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------------


def _lock(db: Session, request_id: str) -> SecretRequest | None:
    return (
        db.query(SecretRequest)
        .filter(SecretRequest.id == request_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )


def closed_reason(req: SecretRequest, now: datetime | None = None) -> str | None:
    """Why `req` takes no answer - its state, or "expired" once past its time
    even before the sweep has run - or None while it is open."""
    if req.state != _OPEN:
        return req.state.value
    if req.expires_at <= (now or utc_now()):
        return SecretRequestState.expired.value
    return None


def _assert_open(req: SecretRequest) -> None:
    reason = closed_reason(req)
    if reason is not None:
        raise AppError(
            410,
            "SECRET_REQUEST_CLOSED",
            "This request no longer takes an answer.",
            details={"reason": reason},
        )


def answer_request(
    db: Session,
    *,
    request_id: str,
    content: str,
    passphrase: str | None,
    by_user: User | None = None,
    by_token: str | None = None,
    request=None,
) -> Secret:
    """Answer a request, as a signed-in target (`by_user`) or through a mailed
    or copied link (`by_token`). The first answer wins. Caller commits."""
    if (by_user is None) == (by_token is None):
        raise ValueError("answer_request needs exactly one of by_user / by_token")
    if not secret_svc.is_enabled(db):
        raise AppError(403, "SECRETS_DISABLED", "Secrets are turned off on this instance.")
    req = _lock(db, request_id)
    if req is None:
        raise AppError(404, "SECRET_REQUEST_NOT_FOUND", "This request does not exist.")
    target: SecretRequestTarget | None
    if by_user is not None:
        target = answering_target(db, req, by_user)
        if target is None:
            if by_user.id == req.requester_id:
                raise AppError(
                    403, "SECRET_REQUEST_SELF", "You cannot answer your own request."
                )
            raise AppError(404, "SECRET_REQUEST_NOT_FOUND", "This request does not exist.")
    else:
        target = target_by_token(db, by_token or "")
        if target.request_id != req.id:
            raise AppError(404, "SECRET_REQUEST_NOT_FOUND", "This request does not exist.")
    _assert_open(req)
    if by_token is not None and not target.token_encrypted:
        # Closed after the token was used or the request ended; the state check
        # above already says so, this is the belt to it.
        raise AppError(410, "SECRET_REQUEST_CLOSED", "This request no longer takes an answer.")
    if not content or len(content) > secret_svc.MAX_CONTENT_CHARS:
        raise AppError(400, "SECRET_CONTENT_INVALID", "The secret must be 1-10,000 characters.")
    requester = db.get(User, req.requester_id)
    if requester is None or requester.is_disabled:
        raise AppError(
            410,
            "SECRET_REQUEST_CLOSED",
            "This request no longer takes an answer.",
            details={"reason": "requester_unavailable"},
        )

    now = utc_now()
    claimed = updated_rows(
        db.execute(
            update(SecretRequest)
            .where(SecretRequest.id == req.id, SecretRequest.state == _OPEN)
            .values(
                state=SecretRequestState.fulfilled,
                fulfilled_at=now,
                ended_at=now,
                fulfilled_by_user_id=by_user.id if by_user is not None else None,
                fulfilled_via_target_id=target.id,
            )
            .execution_options(synchronize_session=False)
        )
    )
    if claimed != 1:
        raise AppError(410, "SECRET_REQUEST_CLOSED", "This request no longer takes an answer.")
    db.refresh(req)

    lim = secret_svc.limits(db)
    lifetime = req.answer_expires_in_sec
    if lim.max_lifetime_days > 0:
        ceiling = lim.max_lifetime_days * 86400
        lifetime = ceiling if lifetime is None else min(lifetime, ceiling)
    keys = (
        (req.kdf_salt, req.kdf_params, req.public_key)
        if req.kdf_salt and req.kdf_params and req.public_key
        else None
    )
    secret = secret_svc.store_sealed(
        db,
        created_by_id=by_user.id if by_user is not None else None,
        content=content,
        label=req.label,
        passphrase=passphrase,
        max_views=req.answer_max_views,
        view_scope=SecretViewScope.per_person,
        expires_at=now + timedelta(seconds=lifetime) if lifetime is not None else None,
        notify_on_view=False,
        burn_on_failures=False,
        lim=lim,
        request_keys=keys,
        request_id=req.id,
        is_answer=True,
        answered_by_email=target.email if target.kind == SecretRecipientKind.email else None,
    )
    db.add(
        SecretRecipient(
            secret_id=secret.id, kind=SecretRecipientKind.user, recipient_user_id=requester.id
        )
    )
    db.add(SecretUserState(secret_id=secret.id, user_id=requester.id))
    _close_tokens(db, req)
    db.flush()

    via = target.kind.value
    record_audit_event(
        db,
        event_type=AuditEventType.secret_created,
        actor_user_id=by_user.id if by_user is not None else None,
        target_type="secret",
        target_id=secret.id,
        metadata={
            "via_request": req.id,
            "answered_via": via,
            "reader_count": 1,
            "max_views": secret.max_views,
            "expires_at": secret.expires_at.isoformat() if secret.expires_at else None,
            "has_passphrase": secret.has_passphrase,
            "has_request_passphrase": secret.has_request_passphrase,
        },
        request=request,
    )
    record_audit_event(
        db,
        event_type=AuditEventType.secret_request_answered,
        actor_user_id=by_user.id if by_user is not None else None,
        target_type="secret_request",
        target_id=req.id,
        metadata={"answered_via": via, "secret_id": secret.id},
        request=request,
    )
    _notify_requester(
        db,
        req,
        requester,
        outcome="answered",
        answered_by=(
            by_user.display_name
            if by_user is not None
            else (target.email if target.kind == SecretRecipientKind.email else None)
        ),
        answered_via=via,
        secret=secret,
    )
    return secret


def _close_tokens(db: Session, req: SecretRequest) -> None:
    for target in db.query(SecretRequestTarget).filter(SecretRequestTarget.request_id == req.id):
        target.token_encrypted = None


def _notify_requester(
    db: Session,
    req: SecretRequest,
    requester: User,
    *,
    outcome: str,
    answered_by: str | None = None,
    answered_via: str | None = None,
    secret: Secret | None = None,
) -> None:
    if requester.is_disabled:
        return
    from . import notification as notif_svc

    url = secret_svc._detail_url(db, secret) if secret is not None else detail_url(db, req)
    notif_svc.dispatch(
        db,
        user=requester,
        category=NotificationCategory.secret_request_update,
        payload={
            "recipient_name": requester.display_name,
            "label": req.label,
            "outcome": outcome,
            "answered_by": answered_by,
            "answered_via": answered_via,
            "secret_url": url,
        },
        link_url=url,
        email_to=requester.email,
    )


# ---------------------------------------------------------------------------
# Peeking (anonymous; costs nothing)
# ---------------------------------------------------------------------------


@dataclass
class PeekResult:
    request: SecretRequest
    requester_name: str | None


def peek(db: Session, token: str) -> PeekResult:
    """What an answer page shows before anything is typed. Refuses a closed
    request with its reason, so the page can say so."""
    target = target_by_token(db, token)
    req = db.get(SecretRequest, target.request_id)
    if req is None:
        raise AppError(404, "SECRET_REQUEST_NOT_FOUND", "This request does not exist.")
    _assert_open(req)
    if not target.token_encrypted:
        raise AppError(410, "SECRET_REQUEST_CLOSED", "This request no longer takes an answer.")
    requester = db.get(User, req.requester_id)
    return PeekResult(request=req, requester_name=requester.display_name if requester else None)


# ---------------------------------------------------------------------------
# Ending
# ---------------------------------------------------------------------------


def end_request(
    db: Session,
    req: SecretRequest,
    state: SecretRequestState,
    *,
    actor: User | None = None,
    reason: str | None = None,
    notify: bool = True,
    request=None,
) -> bool:
    """End an open request without an answer. Returns False when it had
    already ended (the claim is a conditional UPDATE). Caller commits."""
    if state not in _END_EVENTS:
        raise ValueError("end_request takes cancelled or expired")
    db.flush()
    now = utc_now()
    claimed = updated_rows(
        db.execute(
            update(SecretRequest)
            .where(SecretRequest.id == req.id, SecretRequest.state == _OPEN)
            .values(state=state, ended_at=now)
            .execution_options(synchronize_session=False)
        )
    )
    db.refresh(req)
    if claimed != 1:
        return False
    _close_tokens(db, req)
    db.flush()
    record_audit_event(
        db,
        event_type=_END_EVENTS[state],
        actor_user_id=actor.id if actor is not None else None,
        target_type="secret_request",
        target_id=req.id,
        metadata={"reason": reason},
        request=request,
    )
    if notify and state == SecretRequestState.expired:
        requester = db.get(User, req.requester_id)
        if requester is not None:
            _notify_requester(db, req, requester, outcome="expired")
    return True


def cancel_request(db: Session, req: SecretRequest, *, actor: User, request=None) -> None:
    """The requester's (or an admin's) Cancel. Idempotent. Caller commits."""
    if actor.id != req.requester_id and actor.role != UserRole.admin:
        raise AppError(403, "FORBIDDEN", "Only the requester or an admin can do that.")
    end_request(
        db,
        req,
        SecretRequestState.cancelled,
        actor=actor,
        reason="requester" if actor.id == req.requester_id else "admin",
        request=request,
    )


def expire_due(db: Session, *, limit: int = 500) -> int:
    """Expire open requests past their time; the requester is told. Caller commits."""
    now = utc_now()
    due = (
        db.query(SecretRequest)
        .filter(SecretRequest.state == _OPEN, SecretRequest.expires_at <= now)
        .order_by(SecretRequest.expires_at, SecretRequest.id)
        .limit(limit)
        .all()
    )
    return sum(1 for req in due if end_request(db, req, SecretRequestState.expired))


def cancel_all_open(db: Session, *, actor: User | None, reason: str, request=None) -> int:
    """Cancel every open request (a config import - the link tokens are under
    the old instance key). Caller commits."""
    rows = db.query(SecretRequest).filter(SecretRequest.state == _OPEN).all()
    return sum(
        1
        for req in rows
        if end_request(
            db,
            req,
            SecretRequestState.cancelled,
            actor=actor,
            reason=reason,
            notify=False,
            request=request,
        )
    )


# ---------------------------------------------------------------------------
# The requester's links
# ---------------------------------------------------------------------------


def links_for_requester(
    db: Session, req: SecretRequest, *, actor: User, request=None
) -> list[tuple[SecretRequestTarget, str | None]]:
    """The answer links of an open request, readable again by the requester
    alone - never an admin. Every read is audited."""
    if actor.id != req.requester_id:
        raise AppError(403, "FORBIDDEN", "Only the requester can see these links.")
    rows = [
        (t, stored_url(db, t))
        for t in db.query(SecretRequestTarget)
        .filter(
            SecretRequestTarget.request_id == req.id,
            SecretRequestTarget.kind.in_(_ANONYMOUS_KINDS),
        )
        .order_by(SecretRequestTarget.id)
    ]
    record_audit_event(
        db,
        event_type=AuditEventType.secret_request_link_shown,
        actor_user_id=actor.id,
        target_type="secret_request",
        target_id=req.id,
        metadata={"link_count": sum(1 for _, url in rows if url)},
        request=request,
    )
    return rows


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def _asked_request_ids(db: Session, user_id: int):
    """Requests `user_id` may answer: a direct target, or a group they were in
    when asked and are in now."""
    direct = db.query(SecretRequestTarget.request_id).filter(
        SecretRequestTarget.kind == SecretRecipientKind.user,
        SecretRequestTarget.target_user_id == user_id,
    )
    via_group = (
        db.query(SecretRequestTarget.request_id)
        .join(SecretRequestGroupMember, SecretRequestGroupMember.target_id == SecretRequestTarget.id)
        .join(
            GroupMember,
            and_(
                GroupMember.group_id == SecretRequestTarget.target_group_id,
                GroupMember.user_id == SecretRequestGroupMember.user_id,
            ),
        )
        .filter(SecretRequestGroupMember.user_id == user_id)
    )
    return direct.union(via_group)


def _list_query(db: Session, *, states: list[SecretRequestState] | None, q: str):
    query = db.query(SecretRequest).join(User, User.id == SecretRequest.requester_id)
    if states:
        query = query.filter(SecretRequest.state.in_(states))
    term = q.strip()
    if term:
        like = contains(term)
        query = query.filter(
            or_(
                SecretRequest.label.ilike(like, escape=LIKE_ESCAPE),
                User.display_name.ilike(like, escape=LIKE_ESCAPE),
            )
        )
    return query


def _page(query, page: int, page_size: int) -> tuple[list[SecretRequest], int]:
    total = query.count()
    rows = (
        query.order_by(SecretRequest.created_at.desc(), SecretRequest.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total


def list_for_user(
    db: Session,
    *,
    user: User,
    box: str,
    states: list[SecretRequestState] | None,
    q: str,
    page: int,
    page_size: int,
) -> tuple[list[SecretRequest], int]:
    query = _list_query(db, states=states, q=q)
    if box == "mine":
        query = query.filter(SecretRequest.requester_id == user.id)
    else:
        query = query.filter(
            SecretRequest.id.in_(_asked_request_ids(db, user.id).subquery().select()),
            SecretRequest.requester_id != user.id,
        )
    return _page(query, page, page_size)


def list_all(
    db: Session,
    *,
    states: list[SecretRequestState] | None,
    q: str,
    page: int,
    page_size: int,
) -> tuple[list[SecretRequest], int]:
    return _page(_list_query(db, states=states, q=q), page, page_size)


def answer_of(db: Session, req: SecretRequest) -> Secret | None:
    return (
        db.query(Secret)
        .filter(Secret.request_id == req.id)
        .order_by(Secret.created_at, Secret.id)
        .first()
    )


def summary(targets: Iterable[SecretRequestTarget]) -> dict[str, int | bool]:
    users = groups = emails = 0
    link = False
    for t in targets:
        if t.kind == SecretRecipientKind.user:
            users += 1
        elif t.kind == SecretRecipientKind.group:
            groups += 1
        elif t.kind == SecretRecipientKind.email:
            emails += 1
        else:
            link = True
    return {"users": users, "groups": groups, "emails": emails, "link": link}


# ---------------------------------------------------------------------------
# Retention and erasure
# ---------------------------------------------------------------------------


def prune_ended(db: Session, *, older_than_days: int, batch: int = 500) -> int:
    """Delete requests that ended more than `older_than_days` ago. An answer
    stands on its own (its label, `is_answer`, `answered_by_email`), so its
    secret keeps working; `request_id` goes NULL. 0 keeps them. Caller commits."""
    if older_than_days <= 0:
        return 0
    cutoff = utc_now() - timedelta(days=older_than_days)
    rows = (
        db.query(SecretRequest)
        .filter(
            SecretRequest.state != _OPEN,
            SecretRequest.ended_at.isnot(None),
            SecretRequest.ended_at < cutoff,
        )
        .order_by(SecretRequest.ended_at, SecretRequest.id)
        .limit(batch)
        .all()
    )
    for req in rows:
        db.query(Secret).filter(Secret.request_id == req.id).update(
            {Secret.request_id: None}, synchronize_session=False
        )
        db.delete(req)
    db.flush()
    return len(rows)


def erase_user(db: Session, user: User) -> int:
    """Right to erasure: delete the user's requests and every trace of them as a
    target. Their answers to others' requests go with the secrets they sent
    (`secret.erase_user`). Returns how many requests they had made. Caller commits."""
    mine = db.query(SecretRequest).filter(SecretRequest.requester_id == user.id).all()
    for req in mine:
        db.query(Secret).filter(Secret.request_id == req.id).update(
            {Secret.request_id: None}, synchronize_session=False
        )
        db.delete(req)
    db.flush()
    db.query(SecretRequestGroupMember).filter(SecretRequestGroupMember.user_id == user.id).delete(
        synchronize_session=False
    )
    db.query(SecretRequestTarget).filter(
        SecretRequestTarget.kind == SecretRecipientKind.user,
        SecretRequestTarget.target_user_id == user.id,
    ).delete(synchronize_session=False)
    db.query(SecretRequest).filter(SecretRequest.fulfilled_by_user_id == user.id).update(
        {SecretRequest.fulfilled_by_user_id: None}, synchronize_session=False
    )
    db.flush()
    return len(mine)
