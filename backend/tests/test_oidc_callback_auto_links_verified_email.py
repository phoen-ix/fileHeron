"""Phase 10: when an existing local user's verified email matches the
IdP-asserted email, the callback auto-links them — sets
oidc_provider_id + oidc_subject and returns the user. Subsequent
logins through the same provider go via the (provider, sub) match.

This is the "happy path" for an admin who invites a user, who then
clicks the SSO button on Login and ends up signed in without ever
needing the password."""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole
from app.services import oidc as oidc_svc

from ._oidc_helpers import make_claims, patch_exchange


@pytest.mark.asyncio
async def test_callback_auto_links_existing_user_with_verified_email(
    make_provider, make_user, db, monkeypatch
):
    p = make_provider()
    bob = make_user(email="bob@example.com", role=UserRole.client)
    claims = make_claims(
        p, sub="idp-bob", email="bob@example.com",
        name="Bob from IdP", groups=[],
    )
    patch_exchange(monkeypatch, claims)

    user = await oidc_svc.handle_callback(
        db, provider=p, code="x", state_cookie="s", state_param="s",
        expected_nonce=None,
    )
    assert user.id == bob.id
    db.refresh(bob)
    assert bob.oidc_provider_id == p.id
    assert bob.oidc_subject == "idp-bob"

    # And an audit row was written for the link.
    audit_rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.oidc_linked.value)
        .all()
    )
    assert len(audit_rows) == 1
    assert audit_rows[0].extra["via"] == "auto_link"
    assert audit_rows[0].extra["provider_id"] == p.id


@pytest.mark.asyncio
async def test_callback_returns_existing_link_via_provider_sub(
    make_provider, make_user, db, monkeypatch
):
    """Second login: same (provider, sub) → straight return, no relink."""
    p = make_provider()
    user = make_user(email="alice@example.com", role=UserRole.client)
    user.oidc_provider_id = p.id
    user.oidc_subject = "alice-sub"
    db.commit()

    claims = make_claims(
        p, sub="alice-sub", email="alice@example.com", name="Alice",
    )
    patch_exchange(monkeypatch, claims)

    resolved = await oidc_svc.handle_callback(
        db, provider=p, code="x", state_cookie="s", state_param="s",
        expected_nonce=None,
    )
    assert resolved.id == user.id

    # No `oidc_linked` audit on a plain re-login — the link already existed.
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.oidc_linked.value)
        .all()
    )
    assert rows == []


@pytest.mark.asyncio
async def test_callback_does_not_auto_link_user_already_bound_elsewhere(
    make_provider, make_user, db, monkeypatch
):
    """If a local account is already linked to provider B, an unrelated
    callback from provider A must NOT silently overwrite the link."""
    a = make_provider(name="A", issuer_url="https://a.example.com", client_id="a-client")
    b = make_provider(name="B", issuer_url="https://b.example.com", client_id="b-client")

    user = make_user(email="claim@example.com", role=UserRole.client)
    user.oidc_provider_id = b.id
    user.oidc_subject = "b-sub"
    db.commit()

    claims = make_claims(a, sub="a-sub", email="claim@example.com")
    patch_exchange(monkeypatch, claims)

    from app.middleware.errors import AppError

    with pytest.raises(AppError) as exc:
        await oidc_svc.handle_callback(
            db, provider=a, code="x", state_cookie="s", state_param="s",
            expected_nonce=None,
        )
    assert exc.value.code == "OIDC_NO_ACCOUNT"

    db.refresh(user)
    assert user.oidc_provider_id == b.id
    assert user.oidc_subject == "b-sub"
