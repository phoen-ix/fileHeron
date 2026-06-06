"""Phase 10 anonymous-login callback: refuses to auto-create.

Phase 9 created a brand-new user when an OIDC ID token referenced an
unknown email. Phase 10 explicitly drops that path - admin must invite
first. The callback now raises ``OIDC_NO_ACCOUNT``."""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.services import oidc as oidc_svc

from ._oidc_helpers import make_claims, patch_exchange


@pytest.mark.asyncio
async def test_callback_refuses_unknown_email(make_provider, db, monkeypatch):
    p = make_provider()
    claims = make_claims(
        p, sub="outsider-1", email="outsider@example.com",
        name="Outsider", groups=[],
    )
    patch_exchange(monkeypatch, claims)

    with pytest.raises(AppError) as exc:
        await oidc_svc.handle_callback(
            db, provider=p, code="x", state_cookie="s", state_param="s",
            expected_nonce=None,
        )
    assert exc.value.code == "OIDC_NO_ACCOUNT"
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_callback_refuses_when_email_unverified(
    make_provider, make_user, db, monkeypatch
):
    """Even if a local account exists with the same email, refuse to
    auto-link when the IdP has NOT verified the email."""
    from app.models.user import UserRole

    p = make_provider()
    make_user(email="charlie@example.com", role=UserRole.client)
    claims = make_claims(
        p, sub="idp-charlie", email="charlie@example.com",
        email_verified=False, name="Charlie", groups=[],
    )
    patch_exchange(monkeypatch, claims)

    with pytest.raises(AppError) as exc:
        await oidc_svc.handle_callback(
            db, provider=p, code="x", state_cookie="s", state_param="s",
            expected_nonce=None,
        )
    # Phase 10: unverified emails just fall through to "no account",
    # since auto-link is gated on email_verified.
    assert exc.value.code == "OIDC_NO_ACCOUNT"
