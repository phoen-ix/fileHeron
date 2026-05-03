"""WebAuthn service surface tests.

We don't try to exercise the cryptographic ceremony end-to-end here —
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
