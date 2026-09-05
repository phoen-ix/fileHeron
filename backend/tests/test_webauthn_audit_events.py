"""Passkey registration and removal are audited as what they are.

Both routes logged `totp_enabled` / `totp_disabled` ("closest available",
with a comment promising a WebAuthn-specific event "in P8 cleanup" that never
came), so the audit trail claimed TOTP was toggled whenever a passkey was added
or removed - on the one screen an admin reads to reconstruct an account's
second-factor history.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole
from app.models.user_webauthn_credential import UserWebAuthnCredential

PW = "Pass12345678!"


async def _headers(login_as, email: str) -> dict[str, str]:
    token, _ = await login_as(email, PW)
    return {"Authorization": f"Bearer {token}"}


class _FakeRecord:
    id = 4242
    name = "Laptop"
    transports = "internal"
    created_at = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    last_used_at = None


@pytest.mark.asyncio
async def test_registering_a_passkey_audits_webauthn_credential_added(
    make_user, db, client, login_as, monkeypatch
):
    from app.services import webauthn as webauthn_svc

    u = make_user(email="a@test.local", role=UserRole.client, password=PW)
    headers = await _headers(login_as, "a@test.local")

    async def _fake_complete(db_, *, user, credential_response, name):
        return _FakeRecord()

    monkeypatch.setattr(webauthn_svc, "register_complete", _fake_complete)

    r = await client.post(
        "/api/account/webauthn/register/complete",
        json={"name": "Laptop", "credential": {"id": "x"}},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.actor_user_id == u.id, AuditLog.target_type == "webauthn_credential")
        .all()
    )
    assert [row.event_type for row in rows] == [AuditEventType.webauthn_credential_added.value]
    assert rows[0].target_id == "4242"
    assert not any(row.event_type == AuditEventType.totp_enabled.value for row in rows)


@pytest.mark.asyncio
async def test_removing_a_passkey_audits_webauthn_credential_removed(
    make_user, db, client, login_as
):
    u = make_user(email="a@test.local", role=UserRole.client, password=PW)
    cred = UserWebAuthnCredential(
        user_id=u.id,
        credential_id=b"cred-a",
        public_key=b"pk-a",
        sign_count=0,
        transports="usb",
        name="Key",
    )
    db.add(cred)
    db.commit()
    headers = await _headers(login_as, "a@test.local")

    r = await client.delete(f"/api/account/webauthn/{cred.id}", headers=headers)
    assert r.status_code == 204, r.text

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.actor_user_id == u.id, AuditLog.target_type == "webauthn_credential")
        .all()
    )
    assert [row.event_type for row in rows] == [AuditEventType.webauthn_credential_removed.value]
    assert rows[0].target_id == str(cred.id)
    assert not any(row.event_type == AuditEventType.totp_disabled.value for row in rows)
