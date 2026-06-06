"""Admin-controlled 2FA enforcement policy.

Mirrors the API-token / public-link policy pattern: a couple of kv
keys in `app_settings`, resolved on demand. Admin can require TOTP
for any subset of {admin, employee, client} roles, plus any number
of groups. When neither key is set, falls back to the static env knob
``REQUIRE_2FA`` so existing .env-driven deploys keep working.

Replaces the static `users.requires_2fa_setup` flag - the column is
gone (migration 202605021000) and `is_2fa_required` computes the
answer per request from the policy + the user's TOTP state. That
sidesteps the "flag never cleared" bug the old design had
(`confirm_enable` didn't reset it; group-membership and policy
changes didn't either).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable

from sqlalchemy.orm import Session

from ..config import settings
from ..models.group_member import GroupMember
from ..models.user import User, UserRole
from . import settings as settings_svc

logger = logging.getLogger("fileheron.twofa_policy")


ALLOWED_ROLES: frozenset[str] = frozenset({r.value for r in UserRole})


def _env_default_roles() -> set[str]:
    """Translate REQUIRE_2FA={none,admins,all} into the role set."""
    mode = (settings.REQUIRE_2FA or "none").lower()
    if mode == "all":
        return set(ALLOWED_ROLES)
    if mode == "admins":
        return {UserRole.admin.value}
    return set()


def _parse_int_list(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    out: list[int] = []
    for x in parsed:
        try:
            out.append(int(x))
        except (ValueError, TypeError):
            continue
    return out


def _parse_role_list(raw: str | None) -> set[str]:
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return set()
    if not isinstance(parsed, list):
        return set()
    return {r for r in parsed if isinstance(r, str) and r in ALLOWED_ROLES}


def _resolve_policy(db: Session) -> tuple[set[str], list[int], bool]:
    """Returns (required_roles, required_group_ids, is_kv_overridden).

    `is_kv_overridden` is True iff at least one of the two kv keys is
    present (so the SPA can show a "currently inheriting from
    REQUIRE_2FA=…" hint when both are absent).
    """
    raw_roles = settings_svc.get(db, settings_svc.Keys.TWOFA_REQUIRED_ROLES)
    raw_groups = settings_svc.get(db, settings_svc.Keys.TWOFA_REQUIRED_GROUPS)
    is_kv_overridden = raw_roles is not None or raw_groups is not None
    if is_kv_overridden:
        return _parse_role_list(raw_roles), _parse_int_list(raw_groups), True
    return _env_default_roles(), [], False


def has_totp_enabled(user: User) -> bool:
    """Mirrors the boot-time check in the old enforcement code: the
    user has a UserTOTP row with `enabled_at` set."""
    return user.totp is not None and user.totp.enabled_at is not None


def is_2fa_required(db: Session, user: User) -> bool:
    """True iff the user must enable 2FA but hasn't yet."""
    if has_totp_enabled(user):
        return False
    roles, group_ids, _ = _resolve_policy(db)
    if user.role.value in roles:
        return True
    if group_ids:
        hit = (
            db.query(GroupMember.user_id)
            .filter(
                GroupMember.user_id == user.id,
                GroupMember.group_id.in_(group_ids),
            )
            .first()
        )
        if hit is not None:
            return True
    return False


def is_2fa_required_bulk(db: Session, users: list[User]) -> dict[int, bool]:
    """Bulk equivalent of `is_2fa_required` for a page of users.

    Resolves the policy once and issues at most one extra query (group
    membership) instead of re-resolving + re-querying per user - the
    admin-users list hydration path. Relies on each user's `totp`
    relationship already being loaded/loadable; callers that bulk-load
    UserTOTP first avoid the per-user lazy load.
    """
    if not users:
        return {}
    roles, group_ids, _ = _resolve_policy(db)
    member_ids: set[int] = set()
    if group_ids:
        rows = (
            db.query(GroupMember.user_id)
            .filter(
                GroupMember.user_id.in_([u.id for u in users]),
                GroupMember.group_id.in_(group_ids),
            )
            .distinct()
            .all()
        )
        member_ids = {r[0] for r in rows}
    out: dict[int, bool] = {}
    for u in users:
        if has_totp_enabled(u):
            out[u.id] = False
        elif u.role.value in roles:
            out[u.id] = True
        else:
            out[u.id] = u.id in member_ids
    return out


def write_policy(
    db: Session,
    *,
    actor: User,
    required_roles: Iterable[str],
    required_group_ids: Iterable[int],
) -> None:
    """Persist the policy. Caller commits + audits.

    `required_roles=[]` and `required_group_ids=[]` together clear
    enforcement (kv-set, env fallback no longer applies - the kv
    override wins). To revert to env fallback, delete both keys
    explicitly via `settings_svc.set_value(... value=None)` - which
    is what passing empty lists triggers below.
    """
    role_list = sorted({r for r in required_roles if r in ALLOWED_ROLES})
    group_list = sorted({int(g) for g in required_group_ids})

    settings_svc.set_value(
        db,
        key=settings_svc.Keys.TWOFA_REQUIRED_ROLES,
        # Empty list still persists so kv overrides env. Use None
        # only if the operator explicitly wants env fallback.
        value=json.dumps(role_list),
        actor=actor,
    )
    settings_svc.set_value(
        db,
        key=settings_svc.Keys.TWOFA_REQUIRED_GROUPS,
        value=json.dumps(group_list),
        actor=actor,
    )
