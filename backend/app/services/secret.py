"""Secrets (v2.24.0): who may send one, creating it, listing it, ending it.

A secret is a short text (a password, a key) for named people or a link, which
can be read a limited number of times and/or until a date and is then destroyed.
Reading one - eligibility, view counting, passphrase checks - lives in
services/secret_reveal.py; everything else is here.

Rules that hold across both modules:

- The content is readable in exactly one place: the reveal response. Nothing in
  this module decrypts, and every audit row, notification, mail, webhook and
  log line is built from metadata - the label, ids, counts, limits.
- Ending a secret SHREDS it (`end_secret`): the ciphertext, the wrapped key and
  every stored link are NULLed in the same transaction as the state change. The
  claim is a conditional UPDATE, so a reveal's last view and the expiry sweep
  cannot both end it.
- Admins see metadata and may burn a secret early; they can never read it, and
  never read a link back either. Neither can the sender, except that a sender
  may copy their own links again (re-viewable by decision, audited each time).
- An account person may read a secret if they are a direct recipient, or were a
  member of a recipient group when it was sent AND still are. The sender never
  is. `secret_user_states` holds one row per such person from the moment of
  sending, so every counter is a conditional UPDATE, never an insert race.
- Clients can only send to employees they are connected to: no groups, no
  addresses, no link. Email addresses are never looked up (an answer of "this
  address has an account" would tell the sender that a client exists) - the
  same rule as recipients without an account on shares.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session

from ..config import settings
from ..middleware.errors import AppError
from ..models.audit_log import AuditEventType
from ..models.client_employee_connection import ClientEmployeeConnection
from ..models.group import Group
from ..models.group_member import GroupMember
from ..models.notification import NotificationCategory
from ..models.secret import (
    Secret,
    SecretAccessEvent,
    SecretGroupMember,
    SecretRecipient,
    SecretRecipientKind,
    SecretState,
    SecretUserState,
    SecretViewScope,
)
from ..models.user import User, UserRole
from ..utils.columns import declared_width
from ..utils.crypto import (
    SecretUndecryptableError,
    decrypt_setting,
    encrypt_setting,
    random_token,
    seal_secret,
    sha256_hex,
)
from ..utils.dbresult import updated_rows
from ..utils.like import LIKE_ESCAPE, contains
from ..utils.timeutil import utc_now
from . import policy_gate, settings_registry
from . import settings as settings_svc
from .audit import record_audit_event

logger = logging.getLogger("fileheron.secret")

K = settings_svc.Keys

MAX_CONTENT_CHARS = 10_000
MAX_EMAILS = 20
_LABEL_MAX = declared_width(Secret.__table__.c.label)
# The mail to an address with no account, and its mail-log category - listed in
# `mail_log._AUTH_LINK_CATEGORIES`, so the log never offers to resend a live link.
EXTERNAL_TEMPLATE_SLUG = "secret_link_external"
EXTERNAL_MAIL_CATEGORY = EXTERNAL_TEMPLATE_SLUG

PASSPHRASE_MODES = ("lock", "burn")
DEFAULT_PASSPHRASE_MODE = "lock"
# Sending at all: everyone by default, because a client can only reach the
# employees they are connected to - the gate is not the only control.
SEND_DEFAULT_POLICY_MODE = "everyone"
# Sending out of the organisation (an address, the link): staff by default, and a
# client never, whatever the policy says.
EXTERNAL_DEFAULT_POLICY_MODE = "employees_admins"

_ACTIVE = SecretState.active
_ANONYMOUS_KINDS = (SecretRecipientKind.email, SecretRecipientKind.link)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def is_enabled(db: Session) -> bool:
    return settings_svc.get_bool(db, K.SECRETS_ENABLED, default=False)


def _send_keys() -> dict[str, str]:
    return {
        "mode_key": K.SECRETS_SEND_POLICY_MODE,
        "users_key": K.SECRETS_SEND_ALLOWED_USERS,
        "groups_key": K.SECRETS_SEND_ALLOWED_GROUPS,
    }


def _external_keys() -> dict[str, str]:
    return {
        "mode_key": K.SECRETS_EXTERNAL_POLICY_MODE,
        "users_key": K.SECRETS_EXTERNAL_ALLOWED_USERS,
        "groups_key": K.SECRETS_EXTERNAL_ALLOWED_GROUPS,
    }


def resolve_send_policy(db: Session) -> tuple[str, list[int], list[int]]:
    return policy_gate.resolve_policy(
        db, **_send_keys(), default_mode=SEND_DEFAULT_POLICY_MODE
    )


def resolve_external_policy(db: Session) -> tuple[str, list[int], list[int]]:
    return policy_gate.resolve_policy(
        db, **_external_keys(), default_mode=EXTERNAL_DEFAULT_POLICY_MODE
    )


def may_send(db: Session, user: User) -> bool:
    """True if `user` may send a secret at all (to someone with an account)."""
    if not is_enabled(db) or user.is_disabled:
        return False
    return policy_gate.is_allowed(
        db, user, **_send_keys(), default_mode=SEND_DEFAULT_POLICY_MODE
    )


def may_send_external(db: Session, user: User) -> bool:
    """True if `user` may also send to an email address or create a link."""
    if user.role == UserRole.client or not may_send(db, user):
        return False
    return policy_gate.is_allowed(
        db, user, **_external_keys(), default_mode=EXTERNAL_DEFAULT_POLICY_MODE
    )


def passphrase_failure_mode(db: Session) -> str:
    value = settings_svc.get(db, K.SECRETS_PASSPHRASE_FAILURE_MODE)
    return value if value in PASSPHRASE_MODES else DEFAULT_PASSPHRASE_MODE


@dataclass(frozen=True)
class Limits:
    max_views: int
    max_expiry_days: int
    max_lifetime_days: int
    passphrase_failure_mode: str
    passphrase_max_failures: int
    passphrase_rate_limit: int
    passphrase_window_sec: int
    passphrase_lockout_sec: int


def limits(db: Session) -> Limits:
    eff = settings_registry.effective
    return Limits(
        max_views=eff(db, K.SECRETS_MAX_VIEWS),
        max_expiry_days=eff(db, K.SECRETS_MAX_EXPIRY_DAYS),
        max_lifetime_days=eff(db, K.SECRETS_MAX_LIFETIME_DAYS),
        passphrase_failure_mode=passphrase_failure_mode(db),
        passphrase_max_failures=eff(db, K.SECRETS_PASSPHRASE_MAX_FAILURES),
        passphrase_rate_limit=eff(db, K.SECRETS_PASSPHRASE_RATE_LIMIT),
        passphrase_window_sec=eff(db, K.SECRETS_PASSPHRASE_WINDOW_SEC),
        passphrase_lockout_sec=eff(db, K.SECRETS_PASSPHRASE_LOCKOUT_SEC),
    )


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


def secret_url(db: Session, token: str) -> str:
    """The recipient-facing URL for a link token. The ONE builder: the create
    response, the sender's copy and the mail all use it, and `mail_log` masks the
    same base path. The token is in the FRAGMENT: browsers never send it, so no
    proxy, access log or Referer ever carries it."""
    from . import site as site_svc

    return f"{site_svc.get_site_url(db)}{settings.SECRET_LINK_BASE_PATH}#{token}"


def stored_url(db: Session, recipient: SecretRecipient) -> str | None:
    """Rebuild a link from its encrypted token, or None (ended, replaced, or
    undecryptable after a JWT_SECRET rotation without the rotation script)."""
    if not recipient.token_encrypted:
        return None
    try:
        return secret_url(db, decrypt_setting(recipient.token_encrypted))
    except SecretUndecryptableError:
        logger.warning("secret recipient %s: link token undecryptable", recipient.id)
        return None


def recipient_by_token(db: Session, token: str) -> SecretRecipient:
    """The address/link recipient a token belongs to. 404 SECRET_NOT_FOUND."""
    rec = (
        db.query(SecretRecipient)
        .filter(SecretRecipient.token_hash == sha256_hex(token))
        .one_or_none()
    )
    if rec is None or rec.kind not in _ANONYMOUS_KINDS:
        raise AppError(404, "SECRET_NOT_FOUND", "This secret does not exist.")
    return rec


def _new_token_recipient(
    secret: Secret, kind: SecretRecipientKind, *, email: str | None = None
) -> tuple[SecretRecipient, str]:
    token = random_token(32)
    rec = SecretRecipient(
        secret_id=secret.id,
        kind=kind,
        email=email,
        token_hash=sha256_hex(token),
        token_encrypted=encrypt_setting(token),
    )
    return rec, token


def live_link(db: Session, secret: Secret) -> SecretRecipient | None:
    return (
        db.query(SecretRecipient)
        .filter(
            SecretRecipient.secret_id == secret.id,
            SecretRecipient.kind == SecretRecipientKind.link,
            SecretRecipient.revoked_at.is_(None),
        )
        .order_by(SecretRecipient.id.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# Who may read it
# ---------------------------------------------------------------------------


@dataclass
class AccountReach:
    """How one account person reaches a secret: their direct recipient row, and
    the recipient groups they were a member of at sending AND still are."""

    direct: SecretRecipient | None
    groups: list[SecretRecipient]

    @property
    def eligible(self) -> bool:
        return self.direct is not None or bool(self.groups)

    @property
    def via(self) -> SecretRecipient | None:
        return self.direct or (self.groups[0] if self.groups else None)


def _reach_query(db: Session, secret_id: str):
    """(user_id, group recipient row) for every snapshot member still in the
    group - the "member then AND now" rule, in one join."""
    return (
        db.query(SecretGroupMember.user_id, SecretRecipient)
        .join(SecretRecipient, SecretRecipient.id == SecretGroupMember.recipient_id)
        .join(
            GroupMember,
            and_(
                GroupMember.group_id == SecretRecipient.recipient_group_id,
                GroupMember.user_id == SecretGroupMember.user_id,
            ),
        )
        .filter(
            SecretRecipient.secret_id == secret_id,
            SecretRecipient.kind == SecretRecipientKind.group,
        )
    )


def account_reach(db: Session, secret: Secret, user_id: int) -> AccountReach:
    direct = (
        db.query(SecretRecipient)
        .filter(
            SecretRecipient.secret_id == secret.id,
            SecretRecipient.kind == SecretRecipientKind.user,
            SecretRecipient.recipient_user_id == user_id,
        )
        .one_or_none()
    )
    groups = [
        rec
        for _uid, rec in _reach_query(db, secret.id)
        .filter(SecretGroupMember.user_id == user_id)
        .order_by(SecretRecipient.id)
        .all()
    ]
    return AccountReach(direct=direct, groups=groups)


def all_account_reach(db: Session, secret: Secret) -> dict[int, AccountReach]:
    """`account_reach` for every person at once - a handful of queries however
    large the groups, for the completion check and the sender's status page."""
    out: dict[int, AccountReach] = {}
    for rec in db.query(SecretRecipient).filter(
        SecretRecipient.secret_id == secret.id,
        SecretRecipient.kind == SecretRecipientKind.user,
    ):
        if rec.recipient_user_id is not None:
            out[rec.recipient_user_id] = AccountReach(direct=rec, groups=[])
    for uid, rec in _reach_query(db, secret.id).order_by(SecretRecipient.id).all():
        out.setdefault(uid, AccountReach(direct=None, groups=[])).groups.append(rec)
    return out


def _disabled_ids(db: Session, user_ids: Iterable[int]) -> set[int]:
    ids = list(user_ids)
    if not ids:
        return set()
    return {
        uid
        for (uid,) in db.query(User.id).filter(User.id.in_(ids), User.is_disabled.is_(True))
    }


def views_left_for_user(
    secret: Secret, state: SecretUserState, reach: AccountReach
) -> int | None:
    """What this person may still view. None = no view limit."""
    x = secret.max_views
    if x is None:
        return None
    if secret.view_scope == SecretViewScope.total:
        return max(0, x - secret.views_used)
    if secret.view_scope == SecretViewScope.per_person:
        return max(0, x - state.views_used)
    # per_recipient: their own entry's pool if addressed directly, otherwise the
    # pools of the groups they reach it through.
    if reach.direct is not None:
        return max(0, x - reach.direct.views_used)
    return sum(max(0, x - g.views_used) for g in reach.groups)


def views_left_for_recipient(secret: Secret, rec: SecretRecipient) -> int | None:
    """What an address or the link may still view. None = no view limit."""
    x = secret.max_views
    if x is None:
        return None
    if secret.view_scope == SecretViewScope.total:
        return max(0, x - secret.views_used)
    return max(0, x - rec.views_used)


def is_exhausted(db: Session, secret: Secret) -> bool:
    """True when nobody can read the secret any more: the budget is spent, or
    every principal has used their views, burned, been replaced, been disabled or
    left the group. Unlimited views never exhaust while anyone can still read."""
    x = secret.max_views
    if secret.view_scope == SecretViewScope.total and x is not None and secret.views_used >= x:
        return True
    anonymous = (
        db.query(SecretRecipient)
        .filter(
            SecretRecipient.secret_id == secret.id,
            SecretRecipient.kind.in_(_ANONYMOUS_KINDS),
            SecretRecipient.revoked_at.is_(None),
            SecretRecipient.burned_at.is_(None),
        )
        .all()
    )
    for rec in anonymous:
        left = views_left_for_recipient(secret, rec)
        if left is None or left > 0:
            return False
    reach = all_account_reach(db, secret)
    states = (
        db.query(SecretUserState)
        .filter(
            SecretUserState.secret_id == secret.id,
            SecretUserState.burned_at.is_(None),
        )
        .all()
    )
    disabled = _disabled_ids(db, (s.user_id for s in states))
    for state in states:
        r = reach.get(state.user_id)
        if r is None or not r.eligible or state.user_id in disabled:
            continue
        left = views_left_for_user(secret, state, r)
        if left is None or left > 0:
            return False
    return True


# ---------------------------------------------------------------------------
# Creating
# ---------------------------------------------------------------------------


@dataclass
class CreatedSecret:
    secret: Secret
    # Plaintext link token, when a link was created - for the response, once.
    link_token: str | None


def _normalise_expiry(expires_at: datetime | None) -> datetime | None:
    if expires_at is None:
        return None
    if expires_at.tzinfo is not None:
        expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
    return expires_at


def _connected_employee_ids(db: Session, client_id: int) -> set[int]:
    return {
        uid
        for (uid,) in db.query(ClientEmployeeConnection.employee_user_id)
        .filter(ClientEmployeeConnection.client_user_id == client_id)
        .distinct()
    }


def _validate_recipients(
    db: Session, sender: User, user_ids: list[int], group_ids: list[int]
) -> tuple[list[User], list[Group]]:
    users_by_id = (
        {u.id: u for u in db.query(User).filter(User.id.in_(user_ids))} if user_ids else {}
    )
    users: list[User] = []
    for uid in user_ids:
        u = users_by_id.get(uid)
        if u is None or u.is_disabled:
            raise AppError(404, "RECIPIENT_NOT_FOUND", f"Recipient user {uid} is not available.")
        if u.id == sender.id:
            raise AppError(400, "SELF_SHARE", "Cannot share with yourself.")
        users.append(u)
    groups_by_id = (
        {g.id: g for g in db.query(Group).filter(Group.id.in_(group_ids))} if group_ids else {}
    )
    groups: list[Group] = []
    for gid in group_ids:
        g = groups_by_id.get(gid)
        if g is None:
            raise AppError(404, "GROUP_NOT_FOUND", f"Recipient group {gid} is not available.")
        groups.append(g)

    if sender.role == UserRole.client:
        if groups:
            raise AppError(
                403,
                "SECRET_RECIPIENT_NOT_ALLOWED",
                "You can send a secret only to the people you work with.",
            )
        connected = _connected_employee_ids(db, sender.id)
        for u in users:
            if u.id not in connected:
                raise AppError(
                    403, "RECIPIENT_NOT_CONNECTED", f"You're not connected to user {u.id}."
                )
        return users, groups

    # Staff: exactly whom they could address a share to.
    from .share import _validate_outbound_targets

    _validate_outbound_targets(db, sender, users, groups)
    return users, groups


def _member_ids(db: Session, group_id: int) -> list[int]:
    return [
        uid
        for (uid,) in db.query(GroupMember.user_id)
        .join(User, User.id == GroupMember.user_id)
        .filter(GroupMember.group_id == group_id, User.is_disabled.is_(False))
        .order_by(GroupMember.user_id)
    ]


def create_secret(
    db: Session,
    *,
    sender: User,
    content: str,
    label: str | None,
    passphrase: str | None,
    max_views: int | None,
    view_scope: SecretViewScope,
    expires_at: datetime | None,
    user_ids: list[int],
    group_ids: list[int],
    emails: list[str],
    create_link: bool,
    notify_on_view: bool,
    burn_on_failures: bool,
    request=None,
) -> CreatedSecret:
    """Validate, encrypt and store a secret, tell its account recipients and
    mail its addresses (after commit). Caller commits."""
    if not is_enabled(db):
        raise AppError(403, "SECRETS_DISABLED", "Secrets are turned off on this instance.")
    if not may_send(db, sender):
        raise AppError(
            403, "SECRET_NOT_ALLOWED", "Your administrator has restricted who may send secrets."
        )
    emails = list(dict.fromkeys(emails))
    if (emails or create_link) and not may_send_external(db, sender):
        raise AppError(
            403,
            "SECRET_EXTERNAL_NOT_ALLOWED",
            "You may not send a secret to an email address or as a link.",
        )
    if not content or len(content) > MAX_CONTENT_CHARS:
        raise AppError(400, "SECRET_CONTENT_INVALID", "The secret must be 1-10,000 characters.")

    lim = limits(db)
    now = utc_now()
    expires_at = _normalise_expiry(expires_at)
    if max_views is None and expires_at is None:
        raise AppError(400, "SECRET_NEEDS_LIMIT", "Set a view limit, an expiry, or both.")
    if max_views is not None and not 1 <= max_views <= lim.max_views:
        raise AppError(
            400,
            "SECRET_LIMIT_EXCEEDED",
            f"A secret may allow at most {lim.max_views} views.",
            details={"field": "max_views", "max": lim.max_views},
        )
    if expires_at is not None:
        if expires_at <= now:
            raise AppError(400, "EXPIRY_IN_PAST", "Expiry must be in the future.")
        # A minute of slack: the form computes "+90 days" a moment before this.
        if expires_at > now + timedelta(days=lim.max_expiry_days, minutes=1):
            raise AppError(
                400,
                "SECRET_LIMIT_EXCEEDED",
                f"A secret may expire at most {lim.max_expiry_days} days from now.",
                details={"field": "expires_at", "max": lim.max_expiry_days},
            )
    if lim.max_lifetime_days > 0:
        ceiling = now + timedelta(days=lim.max_lifetime_days)
        expires_at = ceiling if expires_at is None else min(expires_at, ceiling)

    user_ids = list(dict.fromkeys(user_ids))
    group_ids = list(dict.fromkeys(group_ids))
    if not user_ids and not group_ids and not emails and not create_link:
        raise AppError(
            400, "SECRET_NO_RECIPIENTS", "Add at least one recipient, or create a link."
        )
    users, groups = _validate_recipients(db, sender, user_ids, group_ids)

    sealed = seal_secret(content, passphrase or None)
    burn_after: int | None = None
    if passphrase and (lim.passphrase_failure_mode == "burn" or burn_on_failures):
        burn_after = lim.passphrase_max_failures

    secret = Secret(
        created_by_id=sender.id,
        label=(label or "").strip()[:_LABEL_MAX] or None,
        ciphertext=sealed.ciphertext,
        key_encrypted=sealed.key_encrypted,
        kdf_salt=sealed.kdf_salt,
        kdf_params=sealed.kdf_params,
        has_passphrase=bool(passphrase),
        max_views=max_views,
        view_scope=view_scope if max_views is not None else SecretViewScope.per_person,
        views_used=0,
        expires_at=expires_at,
        notify_on_view=notify_on_view,
        burn_after_failures=burn_after,
        state=_ACTIVE,
    )
    db.add(secret)
    db.flush()

    readers: list[int] = []
    for u in users:
        db.add(
            SecretRecipient(
                secret_id=secret.id, kind=SecretRecipientKind.user, recipient_user_id=u.id
            )
        )
        readers.append(u.id)
    for g in groups:
        rec = SecretRecipient(
            secret_id=secret.id, kind=SecretRecipientKind.group, recipient_group_id=g.id
        )
        db.add(rec)
        db.flush()
        for uid in _member_ids(db, g.id):
            if uid == sender.id:
                continue
            db.add(SecretGroupMember(recipient_id=rec.id, user_id=uid))
            readers.append(uid)
    mailed: list[tuple[SecretRecipient, str]] = []
    for email in emails:
        rec, token = _new_token_recipient(secret, SecretRecipientKind.email, email=email)
        db.add(rec)
        mailed.append((rec, token))
    link_token: str | None = None
    if create_link:
        rec, link_token = _new_token_recipient(secret, SecretRecipientKind.link)
        db.add(rec)
    reader_ids = list(dict.fromkeys(readers))
    for uid in reader_ids:
        db.add(SecretUserState(secret_id=secret.id, user_id=uid))
    db.flush()

    record_audit_event(
        db,
        event_type=AuditEventType.secret_created,
        actor_user_id=sender.id,
        target_type="secret",
        target_id=secret.id,
        metadata={
            "recipient_user_count": len(users),
            "recipient_group_count": len(groups),
            "reader_count": len(reader_ids),
            # A count, never the addresses: they belong to people with no
            # account, whom erasure cannot reach.
            "email_count": len(emails),
            "has_link": create_link,
            "max_views": max_views,
            "view_scope": secret.view_scope.value,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "has_passphrase": secret.has_passphrase,
            "burn_after_failures": burn_after,
            "notify_on_view": notify_on_view,
        },
        request=request,
    )
    _notify_received(db, secret, sender, reader_ids, direct_ids=[u.id for u in users])
    _mail_addresses(db, secret, sender, mailed)
    return CreatedSecret(secret=secret, link_token=link_token)


# ---------------------------------------------------------------------------
# Notices and mail (metadata only - never the content)
# ---------------------------------------------------------------------------


def _detail_url(db: Session, secret: Secret) -> str:
    from . import site as site_svc

    return f"{site_svc.get_site_url(db)}/secrets/{secret.id}"


def _notify_received(
    db: Session,
    secret: Secret,
    sender: User,
    user_ids: list[int],
    *,
    direct_ids: Iterable[int],
) -> None:
    if not user_ids:
        return
    from . import notification as notif_svc

    url = _detail_url(db, secret)
    base = {
        "sender_name": sender.display_name,
        "label": secret.label,
        "expires_at": secret.expires_at,
        "max_views": secret.max_views,
        "view_scope": secret.view_scope.value,
        "has_passphrase": secret.has_passphrase,
        "secret_url": url,
    }
    direct = set(direct_ids)
    for u in db.query(User).filter(User.id.in_(user_ids)).order_by(User.id):
        payload = dict(base)
        payload["recipient_name"] = u.display_name
        # A member reached only through a group shares the group's views in
        # "each recipient" scope - the mail says so.
        payload["shared_with_group"] = (
            secret.view_scope == SecretViewScope.per_recipient and u.id not in direct
        )
        notif_svc.dispatch(
            db,
            user=u,
            category=NotificationCategory.secret_received,
            payload=payload,
            link_url=url,
            email_to=u.email,
        )


def _mail_addresses(
    db: Session, secret: Secret, sender: User, mailed: list[tuple[SecretRecipient, str]]
) -> None:
    """Mail each address its own link. Rendered per address (each link is
    different), in the sender's locale (nothing is known about the recipient),
    with no unsubscribe footer (no account, no preferences). The passphrase is
    never in it. Sent after the caller commits."""
    if not mailed:
        return
    from . import email as email_svc
    from . import mail_log
    from . import notification as notif_svc
    from . import site as site_svc

    now = utc_now()
    for rec, token in mailed:
        payload = {
            "sender_name": sender.display_name,
            "label": secret.label,
            "link_url": secret_url(db, token),
            "has_passphrase": secret.has_passphrase,
            "expires_at": secret.expires_at,
            "max_views": secret.max_views,
            "view_scope": secret.view_scope.value,
        }
        try:
            subject, text, html = email_svc.render_email(
                sender.locale,
                EXTERNAL_TEMPLATE_SLUG,
                payload,
                app_url=site_svc.get_site_url(db),
                site_timezone=site_svc.get_site_timezone(db),
                app_name=site_svc.get_app_name(db),
                db=db,
            )
        except Exception:
            logger.exception("secret %s: rendering the mail for recipient %s failed", secret.id, rec.id)
            continue
        eid = mail_log.record_queued(
            db,
            recipient_email=rec.email or "",
            recipient_user_id=None,
            category=EXTERNAL_MAIL_CATEGORY,
            template_slug=EXTERNAL_TEMPLATE_SLUG,
            subject=subject,
            text_body=text,
            html_body=html,
        )
        rec.notified_at = now
        notif_svc._queue_email_job(
            db,
            {
                "to": rec.email,
                "subject": subject,
                "text_body": text,
                "html_body": html,
                "email_log_id": eid,
            },
        )
    db.flush()


def notify_viewed(
    db: Session,
    secret: Secret,
    *,
    viewer: User | None,
    rec: SecretRecipient | None,
    ip: str | None,
    views_left: int | None,
) -> None:
    """Tell the sender a view happened, if they asked to be told."""
    if not secret.notify_on_view:
        return
    sender = db.get(User, secret.created_by_id)
    if sender is None or sender.is_disabled:
        return
    from . import notification as notif_svc

    who: str | None
    shown_ip: str | None
    if viewer is not None:
        via, who, shown_ip = "user", viewer.display_name, None
    elif rec is not None and rec.kind == SecretRecipientKind.email:
        via, who, shown_ip = "email", rec.email, ip
    else:
        via, who, shown_ip = "link", None, ip
    url = _detail_url(db, secret)
    notif_svc.dispatch(
        db,
        user=sender,
        category=NotificationCategory.secret_viewed,
        payload={
            "recipient_name": sender.display_name,
            "label": secret.label,
            "via": via,
            "viewer": who,
            "ip": shown_ip,
            "at": utc_now(),
            "views_left": views_left,
            "secret_url": url,
        },
        link_url=url,
        email_to=sender.email,
    )


def _notify_ended(db: Session, secret: Secret, reason: str) -> None:
    sender = db.get(User, secret.created_by_id)
    if sender is None or sender.is_disabled:
        return
    from . import notification as notif_svc

    url = _detail_url(db, secret)
    notif_svc.dispatch(
        db,
        user=sender,
        category=NotificationCategory.secret_ended,
        payload={
            "recipient_name": sender.display_name,
            "label": secret.label,
            "reason": reason,
            "views_used": secret.views_used,
            "secret_url": url,
        },
        link_url=url,
        email_to=sender.email,
    )


# ---------------------------------------------------------------------------
# Ending
# ---------------------------------------------------------------------------

_END_EVENTS = {
    SecretState.burned: AuditEventType.secret_burned,
    SecretState.expired: AuditEventType.secret_expired,
    SecretState.revoked: AuditEventType.secret_revoked,
}


def end_secret(
    db: Session,
    secret: Secret,
    state: SecretState,
    *,
    actor: User | None = None,
    reason: str | None = None,
    notify: bool = True,
    request=None,
) -> bool:
    """End an active secret and shred it. Returns False when it had already
    ended - the claim is a conditional UPDATE, so the last view and the expiry
    sweep cannot both run the side effects. Caller commits."""
    if state == _ACTIVE:
        raise ValueError("end_secret needs a terminal state")
    # Anything pending goes first: the refresh below would otherwise discard it.
    db.flush()
    now = utc_now()
    claimed = updated_rows(
        db.execute(
            update(Secret)
            .where(Secret.id == secret.id, Secret.state == _ACTIVE)
            .values(
                state=state,
                ended_at=now,
                ciphertext=None,
                key_encrypted=None,
                kdf_salt=None,
                kdf_params=None,
            )
            .execution_options(synchronize_session=False)
        )
    )
    db.refresh(secret)
    if claimed != 1:
        return False
    for rec in db.query(SecretRecipient).filter(SecretRecipient.secret_id == secret.id):
        rec.token_encrypted = None
    db.flush()
    record_audit_event(
        db,
        event_type=_END_EVENTS[state],
        actor_user_id=actor.id if actor is not None else None,
        target_type="secret",
        target_id=secret.id,
        metadata={"reason": reason, "views_used": secret.views_used},
        request=request,
    )
    if notify:
        if state == SecretState.revoked and actor is not None and actor.id != secret.created_by_id:
            # Someone else burned the sender's secret: always worth telling them.
            _notify_ended(db, secret, "revoked_by_admin")
        elif secret.notify_on_view and state == SecretState.burned:
            _notify_ended(db, secret, "burned")
        elif secret.notify_on_view and state == SecretState.expired and secret.views_used == 0:
            _notify_ended(db, secret, "expired_unread")
    return True


def end_if_exhausted(db: Session, secret: Secret) -> bool:
    """Burn the secret when nobody can read it any more. Caller commits."""
    if secret.state != _ACTIVE:
        return False
    db.flush()
    db.refresh(secret)
    if not is_exhausted(db, secret):
        return False
    return end_secret(db, secret, SecretState.burned, reason="exhausted")


def burn_now(db: Session, secret: Secret, *, actor: User, request=None) -> None:
    """The sender's (or an admin's) Burn now. Idempotent. Caller commits."""
    if actor.id != secret.created_by_id and actor.role != UserRole.admin:
        raise AppError(403, "FORBIDDEN", "Only the sender or an admin can do that.")
    end_secret(
        db,
        secret,
        SecretState.revoked,
        actor=actor,
        reason="sender" if actor.id == secret.created_by_id else "admin",
        request=request,
    )


def expire_due(db: Session, *, limit: int = 500) -> int:
    """End every active secret past its expiry. Caller commits."""
    now = utc_now()
    due = (
        db.query(Secret)
        .filter(
            Secret.state == _ACTIVE,
            Secret.expires_at.isnot(None),
            Secret.expires_at <= now,
        )
        .order_by(Secret.expires_at, Secret.id)
        .limit(limit)
        .all()
    )
    return sum(1 for s in due if end_secret(db, s, SecretState.expired, reason="expired"))


def sweep_exhausted(db: Session, *, limit: int = 1000) -> int:
    """Burn active secrets nobody can read any more - a member left the group,
    an account was disabled. The reveal path catches its own last view; this
    catches what happens between views. Caller commits."""
    active = (
        db.query(Secret)
        .filter(Secret.state == _ACTIVE)
        .order_by(Secret.created_at, Secret.id)
        .limit(limit)
        .all()
    )
    return sum(1 for s in active if end_if_exhausted(db, s))


def revoke_all_active(db: Session, *, actor: User | None, reason: str, request=None) -> int:
    """End every active secret - a config import, which rewrites the identities
    and groups the recipient rows point at. No notices. Caller commits."""
    active = db.query(Secret).filter(Secret.state == _ACTIVE).order_by(Secret.id).all()
    return sum(
        1
        for s in active
        if end_secret(
            db, s, SecretState.revoked, actor=actor, reason=reason, notify=False, request=request
        )
    )


# ---------------------------------------------------------------------------
# The copyable link
# ---------------------------------------------------------------------------


def _assert_sender(secret: Secret, actor: User) -> None:
    if actor.id != secret.created_by_id:
        raise AppError(403, "FORBIDDEN", "Only the sender can do that.")


def _assert_active(secret: Secret) -> None:
    if secret.state != _ACTIVE:
        raise AppError(
            410,
            "SECRET_ENDED",
            "This secret is no longer available.",
            details={"reason": secret.state.value},
        )


def links_for_sender(
    db: Session, secret: Secret, *, actor: User, request=None
) -> list[tuple[SecretRecipient, str | None]]:
    """The sender's own links, readable again (address links + the live link).
    Each read is audited: a link IS the secret for anyone who opens it. Caller
    commits."""
    _assert_sender(secret, actor)
    rows = (
        db.query(SecretRecipient)
        .filter(
            SecretRecipient.secret_id == secret.id,
            SecretRecipient.kind.in_(_ANONYMOUS_KINDS),
            SecretRecipient.revoked_at.is_(None),
        )
        .order_by(SecretRecipient.id)
        .all()
    )
    out = [(r, stored_url(db, r)) for r in rows]
    record_audit_event(
        db,
        event_type=AuditEventType.secret_link_shown,
        actor_user_id=actor.id,
        target_type="secret",
        target_id=secret.id,
        metadata={"link_count": len(rows)},
        request=request,
    )
    return out


def replace_link(db: Session, secret: Secret, *, actor: User, request=None) -> tuple[SecretRecipient, str]:
    """Create the copyable link, or replace it (the old one stops working; the
    new one starts with a fresh count). Caller commits."""
    _assert_sender(secret, actor)
    _assert_active(secret)
    if not is_enabled(db):
        raise AppError(403, "SECRETS_DISABLED", "Secrets are turned off on this instance.")
    if not may_send_external(db, actor):
        raise AppError(
            403,
            "SECRET_EXTERNAL_NOT_ALLOWED",
            "You may not send a secret to an email address or as a link.",
        )
    old = live_link(db, secret)
    if old is not None:
        old.revoked_at = utc_now()
        old.token_encrypted = None
    rec, token = _new_token_recipient(secret, SecretRecipientKind.link)
    db.add(rec)
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.secret_link_replaced,
        actor_user_id=actor.id,
        target_type="secret",
        target_id=secret.id,
        metadata={"replaced": old is not None},
        request=request,
    )
    return rec, token


def remove_link(db: Session, secret: Secret, *, actor: User, request=None) -> None:
    """Stop the copyable link from working. Caller commits."""
    _assert_sender(secret, actor)
    _assert_active(secret)
    old = live_link(db, secret)
    if old is None:
        raise AppError(404, "SECRET_LINK_NOT_FOUND", "This secret has no link.")
    old.revoked_at = utc_now()
    old.token_encrypted = None
    db.flush()
    record_audit_event(
        db,
        event_type=AuditEventType.secret_link_removed,
        actor_user_id=actor.id,
        target_type="secret",
        target_id=secret.id,
        request=request,
    )
    end_if_exhausted(db, secret)


# ---------------------------------------------------------------------------
# Reading metadata
# ---------------------------------------------------------------------------


def get_or_404(db: Session, secret_id: str) -> Secret:
    secret = db.query(Secret).filter(Secret.id == secret_id).one_or_none()
    if secret is None:
        raise AppError(404, "SECRET_NOT_FOUND", "This secret does not exist.")
    return secret


def user_state(db: Session, secret: Secret, user_id: int) -> SecretUserState | None:
    return db.get(SecretUserState, (secret.id, user_id))


def viewer_role(db: Session, secret: Secret, user: User) -> str | None:
    """"sender" | "admin" | "recipient" | None. Anyone else gets a 404 - a
    secret's existence is not something a non-recipient learns."""
    if user.id == secret.created_by_id:
        return "sender"
    if user_state(db, secret, user.id) is not None:
        return "recipient"
    if user.role == UserRole.admin:
        return "admin"
    return None


def _list_query(db: Session, *, states: list[SecretState] | None, q: str):
    query = db.query(Secret).join(User, User.id == Secret.created_by_id)
    if states:
        query = query.filter(Secret.state.in_(states))
    term = q.strip()
    if term:
        like = contains(term)
        query = query.filter(
            or_(
                Secret.label.ilike(like, escape=LIKE_ESCAPE),
                User.display_name.ilike(like, escape=LIKE_ESCAPE),
            )
        )
    return query


def list_for_user(
    db: Session,
    *,
    user: User,
    box: str,
    states: list[SecretState] | None,
    q: str,
    page: int,
    page_size: int,
) -> tuple[list[Secret], int]:
    query = _list_query(db, states=states, q=q)
    if box == "sent":
        query = query.filter(Secret.created_by_id == user.id)
    else:
        query = query.join(
            SecretUserState,
            and_(
                SecretUserState.secret_id == Secret.id,
                SecretUserState.user_id == user.id,
            ),
        )
    total = query.count()
    rows = (
        query.order_by(Secret.created_at.desc(), Secret.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total


def list_all(
    db: Session,
    *,
    states: list[SecretState] | None,
    q: str,
    page: int,
    page_size: int,
) -> tuple[list[Secret], int]:
    query = _list_query(db, states=states, q=q)
    total = query.count()
    rows = (
        query.order_by(Secret.created_at.desc(), Secret.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total


def recent_events(db: Session, secret: Secret, *, limit: int = 200) -> list[SecretAccessEvent]:
    return (
        db.query(SecretAccessEvent)
        .filter(SecretAccessEvent.secret_id == secret.id)
        .order_by(SecretAccessEvent.created_at.desc(), SecretAccessEvent.id.desc())
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# Retention and erasure
# ---------------------------------------------------------------------------


def prune_ended(db: Session, *, older_than_days: int, batch: int = 500) -> int:
    """Delete the records of secrets that ended more than `older_than_days`
    ago (their content is long gone). 0 keeps them. Caller commits."""
    if older_than_days <= 0:
        return 0
    cutoff = utc_now() - timedelta(days=older_than_days)
    rows = (
        db.query(Secret)
        .filter(
            Secret.state != _ACTIVE,
            Secret.ended_at.isnot(None),
            Secret.ended_at < cutoff,
        )
        .order_by(Secret.ended_at, Secret.id)
        .limit(batch)
        .all()
    )
    for s in rows:
        db.delete(s)
    db.flush()
    return len(rows)


def erase_user(db: Session, user: User) -> int:
    """Right to erasure: delete every secret the user sent, and every trace of
    them as a recipient. Secrets that nobody can read any more as a result are
    burned. Returns how many secrets they had sent. Caller commits."""
    sent = db.query(Secret).filter(Secret.created_by_id == user.id).all()
    for s in sent:
        db.delete(s)
    db.flush()
    touched = {
        sid
        for (sid,) in db.query(SecretUserState.secret_id).filter(
            SecretUserState.user_id == user.id
        )
    }
    db.query(SecretAccessEvent).filter(SecretAccessEvent.user_id == user.id).delete(
        synchronize_session=False
    )
    db.query(SecretGroupMember).filter(SecretGroupMember.user_id == user.id).delete(
        synchronize_session=False
    )
    db.query(SecretUserState).filter(SecretUserState.user_id == user.id).delete(
        synchronize_session=False
    )
    db.query(SecretRecipient).filter(
        SecretRecipient.kind == SecretRecipientKind.user,
        SecretRecipient.recipient_user_id == user.id,
    ).delete(synchronize_session=False)
    db.flush()
    for sid in sorted(touched):
        affected = db.get(Secret, sid)
        if affected is not None:
            db.refresh(affected)
            end_if_exhausted(db, affected)
    return len(sent)
