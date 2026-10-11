"""Passkey sign-in (v2.27.0): one admin switch, `auth.passkey_signin`, off by
default. While it is on, a passkey alone signs in, AND a passkey counts as 2FA,
so a password or SSO sign-in by someone whose second factor is a passkey has to
present it.

The assertions are real - `_webauthn_helpers.SoftPasskey` signs them with a
P-256 key - so py_webauthn's own checks run: signature, challenge, origin, RP
id hash, and the user-verification flag this feature leans on.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config import settings
from app.models.audit_log import AuditEventType, AuditLog
from app.models.login_attempt import LoginAttempt, LoginOutcome
from app.models.user import User, UserRole
from app.models.user_totp import UserTOTP
from app.models.user_webauthn_credential import UserWebAuthnCredential
from app.services import jwt_session
from app.services import settings as settings_svc
from app.services import twofa_policy as twofa_policy_svc

from ._oidc_helpers import pending_from, sso_sign_in
from ._webauthn_helpers import SoftPasskey, install_challenge_store

PASSWORD = "Pass12345678!"


@pytest.fixture(autouse=True)
def _challenges(monkeypatch):
    return install_challenge_store(monkeypatch)


def _switch(db, on: bool) -> None:
    settings_svc.set_value(
        db, key=settings_svc.Keys.PASSKEY_SIGNIN, value="true" if on else "false", actor=None
    )
    db.commit()


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


def _with_passkey(db, make_user, email: str, **kw) -> tuple[User, SoftPasskey]:
    user = make_user(email=email, password=PASSWORD, **kw)
    pk = SoftPasskey(user.id)
    pk.store(db, user.id)
    return user, pk


def _fresh(db, user_id: int) -> User:
    db.expire_all()
    return db.query(User).filter(User.id == user_id).one()


def _attempts(db, outcome: LoginOutcome) -> int:
    return db.query(LoginAttempt).filter(LoginAttempt.outcome == outcome.value).count()


async def _passkey_only(client, pk: SoftPasskey, **assertion_kw):
    begin = await client.post("/api/auth/passkey/begin")
    assert begin.status_code == 200, begin.text
    body = begin.json()
    return await client.post(
        "/api/auth/passkey/complete",
        json={"session": body["session"], "credential": pk.assertion(body["options"], **assertion_kw)},
    )


# ---- switch off: nothing changes -----------------------------------------


@pytest.mark.asyncio
async def test_passkey_signin_is_off_by_default(db, client, make_user):
    _user, pk = _with_passkey(db, make_user, "off@test.local")

    begin = await client.post("/api/auth/passkey/begin")
    complete = await client.post(
        "/api/auth/passkey/complete",
        json={"session": "s", "credential": pk.assertion({"challenge": "c"})},
    )
    config = await client.get("/api/config-public")

    assert begin.status_code == 403 and begin.json()["code"] == "PASSKEY_SIGNIN_DISABLED"
    assert complete.status_code == 403 and complete.json()["code"] == "PASSKEY_SIGNIN_DISABLED"
    assert config.json()["passkey_signin"] is False


@pytest.mark.asyncio
async def test_with_the_switch_off_a_password_alone_still_signs_a_passkey_holder_in(
    db, client, make_user
):
    """No default move: a passkey that was never asked for stays unasked."""
    _with_passkey(db, make_user, "pw@test.local")

    r = await client.post("/api/auth/login", json={"email": "pw@test.local", "password": PASSWORD})

    assert r.status_code == 200, r.text
    assert r.json()["access_token"]


@pytest.mark.parametrize("on", [False, True])
def test_a_passkey_counts_as_2fa_only_with_the_switch_on(db, make_user, on):
    user, _pk = _with_passkey(db, make_user, "req@test.local", role=UserRole.employee)
    settings_svc.set_value(
        db, key=settings_svc.Keys.TWOFA_REQUIRED_ROLES, value='["employee"]', actor=None
    )
    db.commit()
    _switch(db, on)

    assert twofa_policy_svc.is_2fa_required(db, user) is (not on)
    assert twofa_policy_svc.is_2fa_required_bulk(db, [user]) == {user.id: not on}
    assert twofa_policy_svc.has_second_factor(db, user) is on


@pytest.mark.asyncio
@pytest.mark.parametrize("on", [False, True])
async def test_the_admin_user_pages_call_a_passkey_2fa_only_with_the_switch_on(
    db, client, make_user, login_as, on
):
    """"2FA off" beside an account the policy counts as covered would be the
    page contradicting itself."""
    make_user(email="admin@test.local", password=PASSWORD, role=UserRole.admin)
    user, _pk = _with_passkey(db, make_user, "listed@test.local")
    _switch(db, on)
    token, _ = await login_as("admin@test.local", PASSWORD)
    auth = {"Authorization": f"Bearer {token}"}

    listed = await client.get("/api/admin/users", params={"q": "listed@"}, headers=auth)
    detail = await client.get(f"/api/admin/users/{user.id}", headers=auth)

    assert [u["has_2fa"] for u in listed.json()["items"]] == [on]
    assert detail.json()["has_2fa"] is on


@pytest.mark.asyncio
async def test_with_the_switch_off_sso_ignores_a_passkey(
    db, client, monkeypatch, make_provider, make_user
):
    p = make_provider()
    user, _pk = _with_passkey(db, make_user, "ssooff@test.local")
    user.oidc_provider_id, user.oidc_subject = p.id, "sub-off"
    db.commit()

    r = await sso_sign_in(client, monkeypatch, p, sub="sub-off", email="ssooff@test.local")

    assert "/login/2fa" not in r.headers["location"]
    assert "fh_refresh" in r.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_with_the_switch_off_a_pending_sign_in_cannot_use_a_passkey(db, client, make_user):
    user, _pk = _with_passkey(db, make_user, "pendoff@test.local")
    pending = jwt_session.create_pending_2fa_token(user.id, settings, via="oidc")

    r = await client.post("/api/auth/2fa/passkey/begin", json={"pending_token": pending})

    assert r.status_code == 403 and r.json()["code"] == "PASSKEY_SIGNIN_DISABLED"


# ---- switch on: a passkey alone ------------------------------------------


@pytest.mark.asyncio
async def test_a_passkey_alone_signs_in(db, client, make_user):
    user, pk = _with_passkey(db, make_user, "alone@test.local")
    _switch(db, True)

    begin = await client.post("/api/auth/passkey/begin")
    options = begin.json()["options"]
    # No account named, so the browser offers whichever passkeys it holds; and
    # with no password in front of it the passkey must be both factors.
    assert not options.get("allowCredentials")
    assert options["userVerification"] == "required"

    r = await client.post(
        "/api/auth/passkey/complete",
        json={"session": begin.json()["session"], "credential": pk.assertion(options)},
    )

    assert r.status_code == 200, r.text
    assert r.json()["access_token"]
    assert "fh_refresh" in r.headers.get("set-cookie", "")
    me = await client.get(
        "/api/account/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    )
    assert me.json()["id"] == user.id
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.login_success.value)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit is not None and audit.extra["via"] == "passkey"
    cred = db.query(UserWebAuthnCredential).filter(UserWebAuthnCredential.user_id == user.id).one()
    db.refresh(cred)
    assert cred.sign_count == 1 and cred.last_used_at is not None
    assert _fresh(db, user.id).last_login_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["no_user_verification", "wrong_user_handle", "no_user_handle", "forged_signature", "unknown"],
)
async def test_every_bad_passkey_gets_the_same_answer_and_locks_nothing(
    db, client, make_user, case
):
    """One code for every way the passkey itself fails, so which check failed
    is not observable - and none of them counts towards the account lockout. A
    credential id is no secret, so counting would be a lever to lock anyone
    out."""
    user, pk = _with_passkey(db, make_user, "bad@test.local")
    other = make_user(email="other@test.local")
    _switch(db, True)

    kwargs: dict = {}
    if case == "no_user_verification":
        kwargs = {"uv": False}
    elif case == "wrong_user_handle":
        kwargs = {"user_handle": str(other.id).encode()}
    elif case == "no_user_handle":
        kwargs = {"user_handle": None}
    elif case == "forged_signature":
        kwargs = {"signer": SoftPasskey(user.id)}
    elif case == "unknown":
        pk = SoftPasskey(user.id)  # never registered

    for _ in range(8):
        r = await _passkey_only(client, pk, **kwargs)
        assert r.status_code == 401, r.text
        assert r.json()["code"] == "PASSKEY_SIGNIN_FAILED"
        assert "fh_refresh" not in r.headers.get("set-cookie", "")

    fresh = _fresh(db, user.id)
    assert fresh.failed_login_count == 0
    assert fresh.locked_until is None
    assert _attempts(db, LoginOutcome.bad_passkey) == 8


@pytest.mark.asyncio
async def test_a_signin_challenge_is_single_use(db, client, make_user):
    _user, pk = _with_passkey(db, make_user, "once@test.local")
    _switch(db, True)
    begin = (await client.post("/api/auth/passkey/begin")).json()
    payload = {"session": begin["session"], "credential": pk.assertion(begin["options"])}

    first = await client.post("/api/auth/passkey/complete", json=payload)
    replay = await client.post("/api/auth/passkey/complete", json=payload)

    assert first.status_code == 200, first.text
    assert replay.status_code == 400 and replay.json()["code"] == "WEBAUTHN_NO_CHALLENGE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "status", "code"),
    [
        ("disabled", 403, "ACCOUNT_DISABLED"),
        ("locked", 423, "ACCOUNT_LOCKED"),
        ("unverified", 403, "EMAIL_NOT_VERIFIED"),
    ],
)
async def test_a_valid_passkey_does_not_open_a_closed_account(
    db, client, make_user, state, status, code
):
    user, pk = _with_passkey(db, make_user, "closed@test.local")
    if state == "disabled":
        user.is_disabled = True
    elif state == "locked":
        user.locked_until = datetime(2999, 1, 1)
    else:
        user.email_verified = False
    db.commit()
    _switch(db, True)

    r = await _passkey_only(client, pk)

    assert r.status_code == status, r.text
    assert r.json()["code"] == code
    assert "fh_refresh" not in r.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_switching_off_mid_ceremony_refuses_the_finish(db, client, make_user):
    _user, pk = _with_passkey(db, make_user, "mid@test.local")
    _switch(db, True)
    begin = (await client.post("/api/auth/passkey/begin")).json()
    _switch(db, False)

    r = await client.post(
        "/api/auth/passkey/complete",
        json={"session": begin["session"], "credential": pk.assertion(begin["options"])},
    )

    assert r.status_code == 403 and r.json()["code"] == "PASSKEY_SIGNIN_DISABLED"


@pytest.mark.asyncio
async def test_passkey_begin_is_limited_per_ip(db, client, monkeypatch, _challenges):
    from app.services import rate_limit

    _switch(db, True)
    asked: list[str] = []

    def _refuse(bucket, ip, limit, window_sec=900):
        asked.append(bucket)
        return False

    monkeypatch.setattr(rate_limit, "check_ip_allowed", _refuse)

    r = await client.post("/api/auth/passkey/begin")

    assert r.status_code == 429 and r.json()["code"] == "RATE_LIMITED"
    assert asked == ["passkey_signin"]
    assert _challenges == {}, "a refused begin must leave no challenge behind"


# ---- switch on: a passkey as the second factor ---------------------------


@pytest.mark.asyncio
async def test_a_password_alone_no_longer_finishes_for_a_passkey_holder(db, client, make_user):
    """The passkey satisfies the 2FA requirement, so the password alone must not
    finish the sign-in - or the requirement would be met by one factor."""
    user, _pk = _with_passkey(db, make_user, "two@test.local")
    _switch(db, True)

    r = await client.post("/api/auth/login", json={"email": "two@test.local", "password": PASSWORD})

    assert r.status_code == 401 and r.json()["code"] == "WEBAUTHN_REQUIRED"
    assert "fh_refresh" not in r.headers.get("set-cookie", "")
    # Nothing that only a finished sign-in may do happened.
    assert _fresh(db, user.id).last_login_at is None


@pytest.mark.asyncio
async def test_password_then_passkey_finishes_it(db, client, make_user):
    """The web app's existing password + passkey ceremony, unchanged."""
    _user, pk = _with_passkey(db, make_user, "pwpk@test.local")
    _switch(db, True)

    begin = await client.post(
        "/api/auth/webauthn/begin", json={"email": "pwpk@test.local", "password": PASSWORD}
    )
    assert begin.status_code == 200, begin.text
    r = await client.post(
        "/api/auth/webauthn/complete",
        json={
            "session": begin.json()["session"],
            "credential": pk.assertion(begin.json()["options"], uv=False),
        },
    )

    assert r.status_code == 200, r.text
    assert r.json()["access_token"]


@pytest.mark.asyncio
async def test_totp_keeps_its_own_challenge(db, client, make_user):
    user, _pk = _with_passkey(db, make_user, "totp@test.local")
    _enable_totp(db, user.id)
    _switch(db, True)

    r = await client.post("/api/auth/login", json={"email": "totp@test.local", "password": PASSWORD})

    assert r.status_code == 401 and r.json()["code"] == "TOTP_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("totp", "passkey", "methods"),
    [(False, True, ["passkey"]), (True, True, ["totp", "passkey"]), (True, False, ["totp"])],
)
async def test_sso_asks_for_the_second_factor_the_account_has(
    db, client, monkeypatch, make_provider, make_user, totp, passkey, methods
):
    p = make_provider()
    user = make_user(email="sso@test.local", password=PASSWORD)
    user.oidc_provider_id, user.oidc_subject = p.id, "sub-sso"
    db.commit()
    if passkey:
        SoftPasskey(user.id).store(db, user.id)
    if totp:
        _enable_totp(db, user.id)
    _switch(db, True)

    r = await sso_sign_in(client, monkeypatch, p, sub="sub-sso", email="sso@test.local")

    assert r.status_code == 302
    assert "/login/2fa?" in r.headers["location"]
    assert pending_from(r.headers["location"])[1] == methods
    assert "fh_refresh" not in r.headers.get("set-cookie", "")


async def _sso_pending(db, client, monkeypatch, make_provider, make_user, *, email, totp=False):
    p = make_provider()
    user = make_user(email=email, password=PASSWORD)
    user.oidc_provider_id, user.oidc_subject = p.id, f"sub-{email}"
    db.commit()
    pk = SoftPasskey(user.id)
    pk.store(db, user.id)
    if totp:
        _enable_totp(db, user.id)
    _switch(db, True)
    r = await sso_sign_in(client, monkeypatch, p, sub=f"sub-{email}", email=email)
    pending, _methods = pending_from(r.headers["location"])
    return user, pk, pending


@pytest.mark.asyncio
async def test_sso_then_passkey_finishes_the_sign_in(
    db, client, monkeypatch, make_provider, make_user
):
    user, pk, pending = await _sso_pending(
        db, client, monkeypatch, make_provider, make_user, email="ssopk@test.local"
    )

    begin = await client.post("/api/auth/2fa/passkey/begin", json={"pending_token": pending})
    assert begin.status_code == 200, begin.text
    options = begin.json()["options"]
    assert [c["id"] for c in options["allowCredentials"]] == [pk.assertion(options)["id"]]
    # SSO was the knowledge factor; the passkey is possession - no PIN needed.
    assert options["userVerification"] == "preferred"

    r = await client.post(
        "/api/auth/2fa/passkey/complete",
        json={
            "pending_token": pending,
            "session": begin.json()["session"],
            "credential": pk.assertion(options, uv=False),
        },
    )

    assert r.status_code == 200, r.text
    assert "fh_refresh" in r.headers.get("set-cookie", "")
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.login_success.value)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit is not None and audit.extra["via"] == "oidc+passkey"
    assert _fresh(db, user.id).last_login_at is not None


@pytest.mark.asyncio
async def test_a_totp_account_needs_a_verifying_passkey_in_place_of_its_code(
    db, client, monkeypatch, make_provider, make_user
):
    """The same rule as /api/auth/webauthn/*: a passkey stands in for TOTP only
    when it verified the user (PIN / biometric)."""
    user, pk, pending = await _sso_pending(
        db, client, monkeypatch, make_provider, make_user, email="ssototp@test.local", totp=True
    )

    begin = (await client.post("/api/auth/2fa/passkey/begin", json={"pending_token": pending})).json()
    assert begin["options"]["userVerification"] == "required"
    bare = await client.post(
        "/api/auth/2fa/passkey/complete",
        json={
            "pending_token": pending,
            "session": begin["session"],
            "credential": pk.assertion(begin["options"], uv=False),
        },
    )
    assert bare.status_code == 401 and bare.json()["code"] == "WEBAUTHN_VERIFY_FAILED"
    assert _fresh(db, user.id).failed_login_count == 0

    begin = (await client.post("/api/auth/2fa/passkey/begin", json={"pending_token": pending})).json()
    verified = await client.post(
        "/api/auth/2fa/passkey/complete",
        json={
            "pending_token": pending,
            "session": begin["session"],
            "credential": pk.assertion(begin["options"], uv=True),
        },
    )
    assert verified.status_code == 200, verified.text


@pytest.mark.asyncio
async def test_a_passkey_cannot_finish_another_accounts_pending_sign_in(
    db, client, monkeypatch, make_provider, make_user
):
    """The ceremony's session and the pending token are both bearer values the
    caller picks. Start the ceremony on A's pending token, present B's at the
    finish with A's passkey: no session for B."""
    _a, pk_a, pending_a = await _sso_pending(
        db, client, monkeypatch, make_provider, make_user, email="a@test.local"
    )
    b = make_user(email="b@test.local", password=PASSWORD)
    SoftPasskey(b.id).store(db, b.id)
    pending_b = jwt_session.create_pending_2fa_token(b.id, settings, via="oidc")

    begin = (await client.post("/api/auth/2fa/passkey/begin", json={"pending_token": pending_a})).json()
    r = await client.post(
        "/api/auth/2fa/passkey/complete",
        json={
            "pending_token": pending_b,
            "session": begin["session"],
            "credential": pk_a.assertion(begin["options"]),
        },
    )

    assert r.status_code == 401 and r.json()["code"] == "WEBAUTHN_VERIFY_FAILED"
    assert "fh_refresh" not in r.headers.get("set-cookie", "")
    assert _fresh(db, b.id).last_login_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["begin", "complete"])
async def test_a_passkey_first_sign_in_cannot_be_finished_by_a_passkey(
    db, client, make_user, route
):
    """Password + a passkey without verification earns a pending token that owes
    its TOTP code. The same passkey a second time is not that code."""
    user, pk = _with_passkey(db, make_user, "twice@test.local")
    _enable_totp(db, user.id)
    _switch(db, True)
    pending = jwt_session.create_pending_2fa_token(user.id, settings, via="webauthn")

    if route == "begin":
        r = await client.post("/api/auth/2fa/passkey/begin", json={"pending_token": pending})
    else:
        r = await client.post(
            "/api/auth/2fa/passkey/complete",
            json={"pending_token": pending, "session": "s", "credential": pk.assertion({"challenge": "c"})},
        )

    assert r.status_code == 409 and r.json()["code"] == "PASSKEY_ALREADY_PRESENTED"


@pytest.mark.asyncio
async def test_a_locked_account_cannot_finish_a_pending_sign_in_with_a_passkey(
    db, client, monkeypatch, make_provider, make_user
):
    user, _pk, pending = await _sso_pending(
        db, client, monkeypatch, make_provider, make_user, email="lockpk@test.local"
    )
    user.locked_until = datetime(2999, 1, 1)
    db.commit()

    r = await client.post("/api/auth/2fa/passkey/begin", json={"pending_token": pending})

    assert r.status_code == 423 and r.json()["code"] == "ACCOUNT_LOCKED"


# ---- registration ---------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("rk", [True, False, None])
async def test_registration_records_whether_the_passkey_lives_on_the_device(
    db, client, make_user, login_as, rk
):
    user = make_user(email="reg@test.local", password=PASSWORD)
    token, _ = await login_as("reg@test.local", PASSWORD)
    auth = {"Authorization": f"Bearer {token}"}

    begin = await client.post(
        "/api/account/webauthn/register/begin", json={"password": PASSWORD}, headers=auth
    )
    assert begin.status_code == 200, begin.text
    options = begin.json()["options"]
    assert options["extensions"]["credProps"] is True

    r = await client.post(
        "/api/account/webauthn/register/complete",
        json={"name": "laptop", "credential": SoftPasskey(user.id).attestation(options, rk=rk)},
        headers=auth,
    )

    assert r.status_code == 200, r.text
    assert r.json()["discoverable"] is rk
    listed = await client.get("/api/account/webauthn", headers=auth)
    assert [c["discoverable"] for c in listed.json()["items"]] == [rk]


@pytest.mark.asyncio
async def test_a_passkey_registered_here_signs_in_alone(db, client, make_user, login_as):
    """End to end: register through the API, then sign in with nothing else."""
    user = make_user(email="e2e@test.local", password=PASSWORD)
    token, _ = await login_as("e2e@test.local", PASSWORD)
    auth = {"Authorization": f"Bearer {token}"}
    pk = SoftPasskey(user.id)
    begin = (
        await client.post(
            "/api/account/webauthn/register/begin", json={"password": PASSWORD}, headers=auth
        )
    ).json()
    done = await client.post(
        "/api/account/webauthn/register/complete",
        json={"name": "laptop", "credential": pk.attestation(begin["options"])},
        headers=auth,
    )
    assert done.status_code == 200, done.text
    _switch(db, True)

    r = await _passkey_only(client, pk)

    assert r.status_code == 200, r.text


# ---- the switch -------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_admin_switch_is_audited_and_published(db, client, make_user, login_as):
    make_user(email="admin@test.local", password=PASSWORD, role=UserRole.admin)
    token, _ = await login_as("admin@test.local", PASSWORD)
    auth = {"Authorization": f"Bearer {token}"}

    on = await client.put(
        "/api/admin/settings/twofa",
        json={"required_roles": [], "required_group_ids": [], "passkey_signin": True},
        headers=auth,
    )
    assert on.status_code == 200, on.text
    assert on.json()["passkey_signin"] is True
    assert (await client.get("/api/config-public")).json()["passkey_signin"] is True
    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.twofa_policy_changed.value)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert row is not None and row.extra["passkey_signin"] is True

    # A page that predates the switch saves the policy without it: unchanged.
    again = await client.put(
        "/api/admin/settings/twofa",
        json={"required_roles": [], "required_group_ids": []},
        headers=auth,
    )
    assert again.json()["passkey_signin"] is True
    row = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.twofa_policy_changed.value)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert "passkey_signin" not in row.extra

    off = await client.put(
        "/api/admin/settings/twofa",
        json={"required_roles": [], "required_group_ids": [], "passkey_signin": False},
        headers=auth,
    )
    assert off.json()["passkey_signin"] is False
    assert (await client.get("/api/config-public")).json()["passkey_signin"] is False
