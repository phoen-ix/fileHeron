"""Credential replay, state disclosure, and two gates that were simply absent.

authn-3: TOTP anti-replay stamped the CURRENT server step rather than the step
the submitted code belonged to. With a +/-2 window, a code from step N-1 was
accepted, recorded as N, and then still satisfied `last_used_counter < N+1` one
tick later - so a captured code stayed usable for roughly another minute after
its first successful use.

authn-4: the WebAuthn challenge was read with `get`, never deleted. The same
signed assertion could be replayed to /complete for the full 300s TTL.

crypto-10: `verify_token` branched on revoked/expired/disabled BEFORE the
constant-time secret comparison. The 8-hex prefix is not a secret - it is
indexed, logged, and shown in the admin UI - so holding one was enough to learn
whether a token existed and what had happened to it.

authz-6: the SSE stream routes resolve the user themselves instead of going
through `get_actor`, so the mandatory-2FA gate applied to every other
session-authenticated route was absent there.

authn-9: /api/auth/resend-verification minted a token, committed it, stashed it
on `request.state` and returned 200 without sending anything.

All from the 2026-07-30 audit.
"""
from __future__ import annotations

import pyotp
import pytest

from app.middleware.errors import AppError
from app.models.user import UserRole
from app.models.user_totp import UserTOTP
from app.services import api_token as api_token_svc
from app.services import totp as totp_svc
from app.utils.crypto import encrypt_totp_secret
from app.utils.timeutil import utc_now

# --- authn-3: TOTP replay ---------------------------------------------------


@pytest.fixture
def totp_user(db, make_user):
    secret = pyotp.random_base32()
    u = make_user(email="t@test.local", role=UserRole.employee)
    db.add(
        UserTOTP(user_id=u.id, secret_encrypted=encrypt_totp_secret(secret), enabled_at=utc_now())
    )
    db.commit()
    db.refresh(u)
    return u, secret


def test_a_used_code_cannot_be_replayed_from_an_earlier_step(db, totp_user, monkeypatch):
    """The defect itself: submit a code from one step back, then replay it after
    the clock advances. Under the old logic the second attempt succeeded."""
    user, secret = totp_user
    t = pyotp.TOTP(secret)
    now = int(utc_now().replace(tzinfo=None).timestamp())

    class _Clock:
        value = now

    class _FrozenDatetime:
        """`datetime` is immutable, so its `now` cannot be monkeypatched.
        Replace the module's reference to the class instead."""

        @staticmethod
        def now(tz=None):
            import datetime as _dt

            return _dt.datetime.fromtimestamp(_Clock.value, tz=tz)

    monkeypatch.setattr(totp_svc, "datetime", _FrozenDatetime)

    old_step = now // 30 - 1
    code = t.at(old_step * 30)

    assert totp_svc.verify_at_login(db, user=user, code=code) is True

    # One step later the same code is still inside the +/-2 tolerance window.
    _Clock.value = now + 30
    assert totp_svc.verify_at_login(db, user=user, code=code) is False, (
        "a code accepted once was replayable on the next step"
    )


def test_the_counter_records_the_matched_step_not_the_current_one(db, totp_user):
    user, secret = totp_user
    t = pyotp.TOTP(secret)
    now = int(utc_now().replace(tzinfo=None).timestamp())
    old_step = now // 30 - 2

    assert totp_svc.verify_at_login(db, user=user, code=t.at(old_step * 30)) is True
    db.refresh(user.totp)
    assert user.totp.last_used_counter == old_step


def test_a_current_code_still_works(db, totp_user):
    """Control: tightening replay must not start rejecting valid logins."""
    user, secret = totp_user
    assert totp_svc.verify_at_login(db, user=user, code=pyotp.TOTP(secret).now()) is True


def test_immediate_replay_of_the_current_code_is_still_refused(db, totp_user):
    """Control on the original guarantee, which must survive the change."""
    user, secret = totp_user
    code = pyotp.TOTP(secret).now()
    assert totp_svc.verify_at_login(db, user=user, code=code) is True
    assert totp_svc.verify_at_login(db, user=user, code=code) is False


# --- authn-4: WebAuthn challenge single-use ---------------------------------


def test_webauthn_challenges_are_consumed_not_merely_read():
    """`get` leaves the challenge in Redis for its full 300s TTL, so the same
    assertion replays. Asserted at the source level because both call sites are
    async Redis paths with no seam the unit suite can reach."""
    import inspect

    from app.services import webauthn

    src = inspect.getsource(webauthn)
    assert "r.get(f\"{AUTH_KEY}" not in src, "auth challenge is still read non-destructively"
    assert "r.get(f\"{REGISTER_KEY}" not in src, "register challenge is still read non-destructively"
    assert src.count("getdel(") == 2


# --- crypto-10: state disclosure before proof of possession -----------------


@pytest.fixture
def revoked_token(db, make_user):
    owner = make_user(email="owner@test.local", role=UserRole.employee)
    rec, plaintext = api_token_svc.create_token(db, owner=owner, name="t", scopes=None)
    db.commit()
    rec.revoked_at = utc_now()
    db.commit()
    return plaintext, rec


def test_a_wrong_secret_on_a_revoked_token_says_only_invalid(db, revoked_token):
    """The prefix is public. Sending it with garbage must not reveal that a
    token exists and was revoked."""
    plaintext, rec = revoked_token
    prefix = plaintext.split("_")[1]
    forged = f"fh_{prefix}_{'A' * 43}"

    with pytest.raises(AppError) as exc:
        api_token_svc.verify_token(db, token_str=forged)
    assert exc.value.code == "INVALID_API_TOKEN"
    assert "revoked" not in exc.value.message.lower(), (
        "revocation state disclosed to someone who never proved possession"
    )


def test_the_real_holder_still_learns_it_was_revoked(db, revoked_token):
    """Control: the differentiated codes are good UX and must survive for
    whoever actually holds the secret."""
    plaintext, _rec = revoked_token
    with pytest.raises(AppError) as exc:
        api_token_svc.verify_token(db, token_str=plaintext)
    assert "revoked" in exc.value.message.lower()


# --- authn-9: the mail that was never sent ----------------------------------


def test_a_verification_sender_exists():
    """The template, subject, placeholder spec and mail-log masking all existed;
    only the sender was missing, so "resend" returned 200 and did nothing."""
    from app.services import email as email_svc

    assert hasattr(email_svc, "send_verification_email")


def test_resend_verification_actually_sends(monkeypatch):
    """Wired end to end at the router: the endpoint must call the sender."""
    import inspect

    from app.routers import auth as auth_router

    src = inspect.getsource(auth_router.resend_verification)
    assert "send_verification_email" in src
    assert "verify_link_for_dev" not in src, "the dead dev-only stash is still there"
