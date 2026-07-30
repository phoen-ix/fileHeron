"""Losing every admin must not re-open the anonymous setup wizard.

`update_user`'s last-admin guard counted other enabled admins and then mutated in
the same transaction with nothing serialising the two. Two admins demoting EACH
OTHER concurrently both see one other admin and both proceed, leaving zero -
and locking the target row does not help, because they are different rows.

The consequence was the dangerous part: `is_setup_complete` was defined purely as
"at least one non-disabled admin exists", so zero admins re-opened
POST /api/setup/admin - which is anonymous and mounted ungated. Anyone on the
internet could then create themselves an admin on a live instance
(audit 2026-07-30).

Both halves are fixed: the guard re-checks after applying the change, and setup
completion is now a sticky one-way flag so the wizard cannot re-open at all.
"""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.user import UserRole
from app.services import settings as settings_svc
from app.services import setup as setup_svc
from app.services import user_management


def test_setup_is_incomplete_on_a_virgin_instance(db):
    assert setup_svc.is_setup_complete(db) is False


def test_setup_completion_survives_losing_every_admin(db, make_user):
    """The core property: once complete, always complete."""
    settings_svc.set_value(
        db, key=settings_svc.Keys.SETUP_COMPLETED_AT, value="2026-07-30T00:00:00", actor=None
    )
    db.commit()
    # No admin exists at all, yet the wizard must stay shut.
    assert db.query(setup_svc.User).filter(setup_svc.User.role == UserRole.admin).count() == 0
    assert setup_svc.is_setup_complete(db) is True


def test_pre_existing_instances_are_still_recognised(db, make_user):
    """Fallback for instances that completed setup before the flag existed -
    they have an admin but no kv row."""
    make_user(email="boss@test.local", role=UserRole.admin)
    assert settings_svc.get(db, settings_svc.Keys.SETUP_COMPLETED_AT) is None
    assert setup_svc.is_setup_complete(db) is True


def test_last_admin_demotion_is_refused(db, make_user):
    """Control: the original guard must still work in the simple case."""
    boss = make_user(email="boss@test.local", role=UserRole.admin)
    with pytest.raises(AppError) as exc:
        user_management.update_user(
            db, actor=boss, target=boss, role=UserRole.employee,
            display_name=None, is_disabled=None, quota_bytes=None,
        )
    assert exc.value.code == "LAST_ADMIN"


def test_last_admin_disable_is_refused(db, make_user):
    boss = make_user(email="boss@test.local", role=UserRole.admin)
    with pytest.raises(AppError) as exc:
        user_management.update_user(
            db, actor=boss, target=boss, is_disabled=True,
            display_name=None, role=None, quota_bytes=None,
        )
    assert exc.value.code == "LAST_ADMIN"


def test_demoting_one_of_two_admins_is_allowed(db, make_user):
    """Control: the guard must not over-correct into blocking legitimate
    demotions, which would leave admins unable to reorganise."""
    boss = make_user(email="boss@test.local", role=UserRole.admin)
    other = make_user(email="other@test.local", role=UserRole.admin)
    user_management.update_user(
        db, actor=boss, target=other, role=UserRole.employee,
        display_name=None, is_disabled=None, quota_bytes=None,
    )
    db.commit()
    assert other.role == UserRole.employee
