"""Compatibility tests for the 2FA enforcement gate after the
post-Phase 10 refactor.

The boot-time `flag_users_for_2fa_setup` walk + `users.requires_2fa_setup`
column were replaced by an admin-editable kv policy resolved live by
`services.twofa_policy.is_2fa_required`. The full policy + admin
editor coverage lives in `test_admin_twofa_policy.py`; this file
keeps a few focused cases on the gate itself for fast lookup.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import Request

from app.middleware.errors import AppError
from app.models.user import UserRole
from app.models.user_totp import UserTOTP
from app.services import twofa_enforcement as svc


def _fake_request_with_session_auth() -> Request:
    """Build a minimal Request stub the gate can read auth_via from."""
    req = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})
    req.state.auth_via = "session"
    return req


def _enable_totp(db, user_id: int) -> None:
    db.add(
        UserTOTP(
            user_id=user_id,
            secret_encrypted=b"dummy",
            enabled_at=datetime.now(tz=timezone.utc).replace(tzinfo=None),
            last_used_counter=0,
        )
    )
    db.commit()


def test_gate_passes_when_no_policy_and_env_off(make_user, db, monkeypatch):
    """Default env (REQUIRE_2FA=none) + no kv → no enforcement."""
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "REQUIRE_2FA", "none")
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    # No exception expected - should be a quiet no-op.
    svc.require_2fa_complete(_fake_request_with_session_auth(), user=admin, db=db)


def test_gate_blocks_admin_when_env_admins(make_user, db, monkeypatch):
    """Env fallback still works when no kv override is saved."""
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "REQUIRE_2FA", "admins")
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    with pytest.raises(AppError) as exc:
        svc.require_2fa_complete(_fake_request_with_session_auth(), user=admin, db=db)
    assert exc.value.code == "TWOFA_SETUP_REQUIRED"


def test_gate_passes_when_user_has_totp(make_user, db, monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "REQUIRE_2FA", "all")
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    _enable_totp(db, admin.id)
    db.refresh(admin)

    svc.require_2fa_complete(_fake_request_with_session_auth(), user=admin, db=db)


def test_gate_short_circuits_for_api_token_auth(make_user, db, monkeypatch):
    """API tokens are session-less and trusted-at-issuance - the gate
    must not block them even when policy would otherwise require 2FA."""
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "REQUIRE_2FA", "all")
    admin = make_user(email="admin@test.local", role=UserRole.admin)

    req = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})
    req.state.auth_via = "api_token"
    # No exception - the api_token short-circuit kicks in.
    svc.require_2fa_complete(req, user=admin, db=db)
