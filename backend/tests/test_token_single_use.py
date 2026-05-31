"""Regression: single-use token consumption (finding M6).

The consume paths now claim the token via an atomic guarded UPDATE
(`... WHERE used_at IS NULL` + rowcount). Serial double-consume must be
refused; this also exercises the new rowcount gate.
"""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.user_recovery_code import UserRecoveryCode
from app.services import auth as auth_svc
from app.services import hibp as hibp_svc
from app.services import totp as totp_svc
from app.utils.crypto import argon2_hash


@pytest.mark.asyncio
async def test_password_reset_token_is_single_use(make_user, db, monkeypatch):
    async def _not_breached(_pw, _db=None):
        return False

    # The reject now flows through hibp.assert_password_not_breached, which
    # calls the module-global is_password_breached in `hibp`.
    monkeypatch.setattr(hibp_svc, "is_password_breached", _not_breached)

    user = make_user(email="reset@test.local", password="OldPassword123!")
    result = auth_svc.begin_password_reset(db, email="reset@test.local", request=None)
    assert result is not None
    _, token = result
    db.commit()

    # First consume succeeds.
    await auth_svc.consume_password_reset(
        db, plaintext_token=token, new_password="BrandNewPassword456!", request=None
    )
    db.commit()

    # Second consume of the same token is refused.
    with pytest.raises(AppError) as exc:
        await auth_svc.consume_password_reset(
            db, plaintext_token=token, new_password="AttackerPassword789!", request=None
        )
    assert exc.value.status_code == 410
    assert exc.value.code == "RESET_TOKEN_USED"


def test_email_verify_token_is_single_use(make_user, db):
    user = make_user(email="verify@test.local", email_verified=False)
    token = auth_svc.begin_email_verification(db, user=user)
    db.commit()

    auth_svc.consume_email_verification(db, plaintext_token=token, request=None)
    db.commit()

    with pytest.raises(AppError) as exc:
        auth_svc.consume_email_verification(db, plaintext_token=token, request=None)
    assert exc.value.status_code == 410
    assert exc.value.code == "VERIFY_TOKEN_USED"


def test_recovery_code_is_single_use(make_user, db):
    user = make_user(email="rc@test.local")
    db.add(UserRecoveryCode(user_id=user.id, code_hash=argon2_hash("ABCD2345")))
    db.commit()

    assert totp_svc.consume_recovery_code(db, user=user, code="ABCD2345", request=None) is True
    db.commit()
    # Same code again → already used → False.
    assert totp_svc.consume_recovery_code(db, user=user, code="ABCD2345", request=None) is False
