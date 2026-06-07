"""Shared create-policy gate for API tokens and public links.

Both features expose the same admin-tunable shape: a policy ``mode`` plus an
additive user/group allowlist, stored in ``app_settings`` under
feature-specific keys. This module is the single implementation; the feature
services (``api_token`` / ``public_link``) pass their own settings keys and keep
thin ``_resolve_policy`` / ``is_allowed_to_create`` wrappers so router callsites,
tests, and the documented single-gate contract stay unchanged.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from ..models.group_member import GroupMember
from ..models.user import User, UserRole

POLICY_MODES = ("everyone", "employees_admins", "admins_only", "disabled")
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
    db: Session, *, mode_key: str, users_key: str, groups_key: str
) -> tuple[str, list[int], list[int]]:
    """Read ``(mode, allowed_user_ids, allowed_group_ids)`` from app_settings.

    Falls back to ``DEFAULT_POLICY_MODE`` + empty allowlists so an unconfigured
    deploy keeps working until an admin sets a stricter policy.
    """
    from . import settings as settings_svc

    mode = settings_svc.get(db, mode_key) or DEFAULT_POLICY_MODE
    if mode not in POLICY_MODES:
        mode = DEFAULT_POLICY_MODE
    user_ids = _parse_id_list(settings_svc.get(db, users_key))
    group_ids = _parse_id_list(settings_svc.get(db, groups_key))
    return mode, user_ids, group_ids


def is_allowed(
    db: Session, user: User, *, mode_key: str, users_key: str, groups_key: str
) -> bool:
    """True if ``user`` may create under the active policy.

    Admin always passes (operator escape hatch). The allowlist is additive on
    top of the base mode.
    """
    if user.role == UserRole.admin:
        return True
    mode, allowed_users, allowed_groups = resolve_policy(
        db, mode_key=mode_key, users_key=users_key, groups_key=groups_key
    )
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
