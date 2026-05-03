"""Phase 10: explicit Connect flow.

A user who has signed in with their password clicks Connect on the SSO
panel in /account, completes a round-trip with the IdP, and is bound
to their fileHeron account. Refuses if:

- The IdP-asserted email doesn't match the authed user (OIDC_EMAIL_MISMATCH).
- The (provider, sub) is already bound to another user (OIDC_SUBJECT_TAKEN).
- The user is already linked to a different provider (OIDC_ALREADY_LINKED).
"""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import UserRole
from app.services import oidc as oidc_svc

from ._oidc_helpers import make_claims, patch_exchange


@pytest.mark.asyncio
async def test_connect_succeeds_when_email_matches(
    make_provider, make_user, db, monkeypatch
):
    p = make_provider()
    user = make_user(email="hannah@example.com", role=UserRole.employee)
    claims = make_claims(p, sub="idp-hannah", email="hannah@example.com", name="Hannah")
    patch_exchange(monkeypatch, claims)

    out = await oidc_svc.handle_connect_callback(
        db, provider=p, user=user, code="x", state_cookie="s", state_param="s",
        expected_nonce=None,
    )
    assert out.id == user.id
    db.refresh(user)
    assert user.oidc_provider_id == p.id
    assert user.oidc_subject == "idp-hannah"

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.oidc_linked.value)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].extra["via"] == "explicit_connect"


@pytest.mark.asyncio
async def test_connect_refuses_on_email_mismatch(
    make_provider, make_user, db, monkeypatch
):
    p = make_provider()
    user = make_user(email="hannah@example.com", role=UserRole.employee)
    claims = make_claims(
        p, sub="idp-someone-else", email="imposter@example.com",
    )
    patch_exchange(monkeypatch, claims)

    with pytest.raises(AppError) as exc:
        await oidc_svc.handle_connect_callback(
            db, provider=p, user=user, code="x", state_cookie="s", state_param="s",
            expected_nonce=None,
        )
    assert exc.value.code == "OIDC_EMAIL_MISMATCH"

    db.refresh(user)
    assert user.oidc_provider_id is None
    assert user.oidc_subject is None


@pytest.mark.asyncio
async def test_connect_refuses_when_already_linked_elsewhere(
    make_provider, make_user, db, monkeypatch
):
    a = make_provider(name="A", issuer_url="https://a.example.com", client_id="a-client")
    b = make_provider(name="B", issuer_url="https://b.example.com", client_id="b-client")
    user = make_user(email="dual@example.com", role=UserRole.employee)
    user.oidc_provider_id = a.id
    user.oidc_subject = "a-sub"
    db.commit()

    claims = make_claims(b, sub="b-sub", email="dual@example.com")
    patch_exchange(monkeypatch, claims)

    with pytest.raises(AppError) as exc:
        await oidc_svc.handle_connect_callback(
            db, provider=b, user=user, code="x", state_cookie="s", state_param="s",
            expected_nonce=None,
        )
    assert exc.value.code == "OIDC_ALREADY_LINKED"


@pytest.mark.asyncio
async def test_connect_refuses_when_subject_already_taken(
    make_provider, make_user, db, monkeypatch
):
    """Two distinct users both authenticate via the same Entra account →
    second user's connect must refuse."""
    p = make_provider()
    first = make_user(email="first@example.com", role=UserRole.employee)
    first.oidc_provider_id = p.id
    first.oidc_subject = "shared-sub"
    db.commit()

    second = make_user(email="second@example.com", role=UserRole.employee)
    claims = make_claims(p, sub="shared-sub", email="second@example.com")
    patch_exchange(monkeypatch, claims)

    with pytest.raises(AppError) as exc:
        await oidc_svc.handle_connect_callback(
            db, provider=p, user=second, code="x", state_cookie="s", state_param="s",
            expected_nonce=None,
        )
    assert exc.value.code == "OIDC_SUBJECT_TAKEN"


def test_unlink_clears_link_and_audits(make_provider, make_user, db):
    p = make_provider()
    user = make_user(email="leave@example.com", role=UserRole.employee)
    user.oidc_provider_id = p.id
    user.oidc_subject = "leave-sub"
    db.commit()

    oidc_svc.unlink(db, user=user)
    db.commit()

    db.refresh(user)
    assert user.oidc_provider_id is None
    assert user.oidc_subject is None

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.oidc_unlinked.value)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].extra["provider_id"] == p.id


def test_unlink_when_not_linked_is_noop(make_user, db):
    user = make_user(email="never@example.com", role=UserRole.employee)
    oidc_svc.unlink(db, user=user)
    # No audit row for an unchanged state.
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == AuditEventType.oidc_unlinked.value)
        .all()
    )
    assert rows == []
