"""WebAuthn service surface tests.

We don't try to exercise the cryptographic ceremony end-to-end here -
that requires a real authenticator. Instead we test the lifecycle around
it: list, delete, "no credentials" error, options shape.
"""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.user import UserRole
from app.models.user_webauthn_credential import UserWebAuthnCredential
from app.services import webauthn as webauthn_svc


def test_list_credentials_empty(make_user, db):
    user = make_user(email="u@test.local", role=UserRole.client)
    creds = webauthn_svc.list_credentials_for(db, user.id)
    assert creds == []


def test_list_credentials_returns_user_only(make_user, db):
    a = make_user(email="a@test.local", role=UserRole.client)
    b = make_user(email="b@test.local", role=UserRole.client)
    db.add(
        UserWebAuthnCredential(
            user_id=a.id,
            credential_id=b"cred-a",
            public_key=b"pk-a",
            sign_count=0,
            transports="usb",
            name="A's key",
        )
    )
    db.add(
        UserWebAuthnCredential(
            user_id=b.id,
            credential_id=b"cred-b",
            public_key=b"pk-b",
            sign_count=0,
            transports="internal",
            name="B's passkey",
        )
    )
    db.commit()
    creds_a = webauthn_svc.list_credentials_for(db, a.id)
    creds_b = webauthn_svc.list_credentials_for(db, b.id)
    assert len(creds_a) == 1
    assert creds_a[0].name == "A's key"
    assert len(creds_b) == 1


def test_delete_credential_owner_only(make_user, db):
    a = make_user(email="a@test.local", role=UserRole.client)
    b = make_user(email="b@test.local", role=UserRole.client)
    cred = UserWebAuthnCredential(
        user_id=a.id,
        credential_id=b"cred-a",
        public_key=b"pk-a",
        sign_count=0,
        transports="usb",
        name="A's key",
    )
    db.add(cred)
    db.commit()
    # B can't delete A's credential.
    with pytest.raises(AppError) as exc:
        webauthn_svc.delete_credential(db, user=b, credential_db_id=cred.id)
    assert exc.value.code == "WEBAUTHN_NOT_FOUND"
    # A can.
    webauthn_svc.delete_credential(db, user=a, credential_db_id=cred.id)
    db.commit()
    assert webauthn_svc.list_credentials_for(db, a.id) == []


@pytest.mark.asyncio
async def test_authenticate_begin_requires_credentials(make_user, db):
    user = make_user(email="u@test.local", role=UserRole.client)
    with pytest.raises(AppError) as exc:
        await webauthn_svc.authenticate_begin(
            db, user=user, session_key="anything"
        )
    assert exc.value.code == "WEBAUTHN_NO_CREDENTIALS"


class _FakeRedis:
    """Enough of the async client for the challenge store: set + aclose."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_authenticate_begin_user_verification_follows_the_flag(make_user, db, monkeypatch):
    """`require_user_verification` is what the login route sets for a
    TOTP-enrolled account, so the browser REQUIRES a PIN / biometric and the
    assertion can count as the second factor. Everyone else keeps PREFERRED."""
    user = make_user(email="u@test.local", role=UserRole.client)
    db.add(
        UserWebAuthnCredential(
            user_id=user.id,
            credential_id=b"cred-a",
            public_key=b"pk-a",
            sign_count=0,
            transports="usb",
            name="A's key",
        )
    )
    db.commit()
    monkeypatch.setattr(webauthn_svc, "_redis", lambda: _FakeRedis())

    relaxed = await webauthn_svc.authenticate_begin(db, user=user, session_key="s1")
    strict = await webauthn_svc.authenticate_begin(
        db, user=user, session_key="s2", require_user_verification=True
    )

    assert relaxed["userVerification"] == "preferred"
    assert strict["userVerification"] == "required"


def test_sign_count_atomic_update_rejects_concurrent_overwrite(make_user, db):
    """Wave 1 P1-3 regression. The atomic UPDATE in
    authenticate_complete (`WHERE sign_count == record.sign_count`)
    closes the cloned-authenticator gap: two concurrent verifications
    both seeing sign_count=5 can't both write new values - the second's
    WHERE clause no longer matches after the first commits, so its
    rowcount=0 triggers WEBAUTHN_VERIFY_FAILED. Pre-fix unconditional
    assignment would silently let both succeed, defeating the whole
    point of sign-count clone detection.

    SQLite + asyncio is cooperative - we can't reproduce true thread
    parallelism. Instead we exercise the atomic primitive directly: two
    UPDATEs with the same expected-sign-count, second one returns
    rowcount=0 (which the production code converts to AppError).
    """
    from sqlalchemy import update as sql_update

    user = make_user(email="alice@test.local", role=UserRole.client)
    cred = UserWebAuthnCredential(
        user_id=user.id,
        credential_id=b"cred-x",
        public_key=b"pk-x",
        sign_count=5,
        transports="internal",
        name="Test passkey",
    )
    db.add(cred)
    db.commit()

    # Request A: sees sign_count=5, wants to write 7.
    result_a = db.execute(
        sql_update(UserWebAuthnCredential)
        .where(
            UserWebAuthnCredential.id == cred.id,
            UserWebAuthnCredential.sign_count == 5,
        )
        .values(sign_count=7)
    )
    db.flush()
    assert result_a.rowcount == 1

    # Request B: still thinks sign_count=5 (stale read), tries to write 6.
    # WHERE sign_count == 5 no longer matches.
    result_b = db.execute(
        sql_update(UserWebAuthnCredential)
        .where(
            UserWebAuthnCredential.id == cred.id,
            UserWebAuthnCredential.sign_count == 5,
        )
        .values(sign_count=6)
    )
    db.flush()
    assert result_b.rowcount == 0, "stale-sign-count update must not match"

    db.refresh(cred)
    assert cred.sign_count == 7, "first writer wins; second's write was rejected"
