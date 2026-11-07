"""Shared create-policy gate for API tokens, public links and secrets.

Both features expose the same admin-tunable shape: a policy ``mode`` plus an
additive user/group allowlist, stored in ``app_settings`` under
feature-specific keys. This module is the single implementation; the feature
services (``api_token`` / ``public_link``) pass their own settings keys and keep
thin ``resolve_policy`` / ``is_allowed_to_create`` wrappers so router callsites,
tests, and the documented single-gate contract stay unchanged.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from ..middleware.errors import AppError
from ..models.group import Group
from ..models.group_member import GroupMember
from ..models.user import User, UserRole

POLICY_MODES = ("everyone", "employees_admins", "admins_only")
# Default for the public-link + API-token create gates. employees_admins (not
# everyone) so an UNCONFIGURED deploy doesn't let every client mint anonymous
# public download links or long-lived API tokens out of the box (audit L27). An
# admin can widen to "everyone" or allowlist specific clients/groups.
DEFAULT_POLICY_MODE = "employees_admins"


def _parse_id_list(raw: str | None) -> list[int]:
    try:
        return [int(x) for x in json.loads(raw or "[]")]
    except (ValueError, TypeError):
        return []


def resolve_policy(
    db: Session,
    *,
    mode_key: str,
    users_key: str,
    groups_key: str,
    default_mode: str = DEFAULT_POLICY_MODE,
) -> tuple[str, list[int], list[int]]:
    """Read ``(mode, allowed_user_ids, allowed_group_ids)`` from app_settings.

    Falls back to ``default_mode`` + empty allowlists so an unconfigured deploy
    keeps working until an admin sets a stricter policy. A feature passes its own
    default only where its gate is not the only control (secrets: a client can
    only ever reach the employees they are connected to).
    """
    from . import settings as settings_svc

    mode = settings_svc.get(db, mode_key) or default_mode
    if mode == "disabled":
        # Legacy mode (removed v1.51) - collapse to its functional equivalent.
        # Do NOT let it fall through to DEFAULT_POLICY_MODE below: that would
        # LOOSEN an un-migrated deploy or a config-backup import from admin-only
        # to employees_admins.
        mode = "admins_only"
    if mode not in POLICY_MODES:
        mode = default_mode
    user_ids = _parse_id_list(settings_svc.get(db, users_key))
    group_ids = _parse_id_list(settings_svc.get(db, groups_key))
    return mode, user_ids, group_ids


def is_allowed(
    db: Session,
    user: User,
    *,
    mode_key: str,
    users_key: str,
    groups_key: str,
    default_mode: str = DEFAULT_POLICY_MODE,
) -> bool:
    """True if ``user`` may create under the active policy.

    Admin always passes (operator escape hatch). The allowlist is additive on
    top of the base mode.
    """
    if user.role == UserRole.admin:
        return True
    mode, allowed_users, allowed_groups = resolve_policy(
        db,
        mode_key=mode_key,
        users_key=users_key,
        groups_key=groups_key,
        default_mode=default_mode,
    )
    return passes(db, user, mode, allowed_users, allowed_groups)


def passes(
    db: Session, user: User, mode: str, allowed_users: list[int], allowed_groups: list[int]
) -> bool:
    """The base mode plus the additive user/group allowlist, for a resolved
    policy. Shared with the share-approval approver set, which has its own
    switch and resolver but the same shape - its copy of this had already
    drifted by one branch (audit 2026-11-07)."""
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


def assert_ids_exist(db: Session, *, user_ids: list[int], group_ids: list[int]) -> None:
    """400 USER_NOT_FOUND / GROUP_NOT_FOUND with the missing ids when an
    allowlist names a user or group that does not exist. Every admin policy
    PUT runs it; two routers carried the same 38 lines (audit 2026-11-07)."""
    if user_ids:
        found = {row[0] for row in db.query(User.id).filter(User.id.in_(user_ids)).all()}
        missing = [uid for uid in user_ids if uid not in found]
        if missing:
            raise AppError(
                400,
                "USER_NOT_FOUND",
                "One or more selected users do not exist.",
                details={"missing_user_ids": missing},
            )
    if group_ids:
        found = {row[0] for row in db.query(Group.id).filter(Group.id.in_(group_ids)).all()}
        missing = [gid for gid in group_ids if gid not in found]
        if missing:
            raise AppError(
                400,
                "GROUP_NOT_FOUND",
                "One or more selected groups do not exist.",
                details={"missing_group_ids": missing},
            )
