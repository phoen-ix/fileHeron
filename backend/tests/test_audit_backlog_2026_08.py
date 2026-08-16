"""Fixes for the 2026-08-15 audit backlog's recorded LOWs.

One file rather than nine, because these are one wave rather than one topic.
Each test names the item it pins and is written so that reverting the fix turns
it red — several of these exist precisely because the original evidence was a
test that passed against absent code.
"""
from __future__ import annotations

import pytest

from app.models.user import UserRole

PW = "Pass12345678!"


# --- P4: email_verified was a cast, not a check ----------------------------


def _claims_verified(claims: dict) -> bool:
    from app.models.oidc_provider import OIDCPreset, OIDCProvider
    from app.services.oidc import _extract_email

    provider = OIDCProvider(preset=OIDCPreset.custom, issuer_url="https://idp.test")
    return _extract_email(claims, provider)[1]


def test_a_string_email_verified_claim_is_not_believed():
    """`bool("false")` is True. An IdP emitting this claim as a JSON string -
    out of spec, but real - therefore asserted the verification it was actively
    denying, and the auto-link gate believed it."""
    assert _claims_verified({"email": "a@x.test", "email_verified": "false"}) is False
    assert _claims_verified({"email": "a@x.test", "email_verified": "no"}) is False
    assert _claims_verified({"email": "a@x.test", "email_verified": ""}) is False
    # Positive control: the conformant shapes must still pass, or the fix has
    # simply broken SSO instead.
    assert _claims_verified({"email": "a@x.test", "email_verified": True}) is True
    assert _claims_verified({"email": "a@x.test", "email_verified": "true"}) is True
    assert _claims_verified({"email": "a@x.test"}) is False


# --- P9: cancel racing a confirm reported success --------------------------


def test_the_self_cancel_is_a_single_conditional_update():
    """Pins the mechanism, following the precedent this repo already set for the
    sibling branch (`test_supersede_is_a_single_conditional_update`): the race
    is a true interleaving and SQLite cannot exhibit it, so the thing worth
    asserting is that the write carries its own state predicates.

    The self/admin branch was a read-then-assign — SQLAlchemy emitted
    `UPDATE ... WHERE id = ?` with no state predicate — so a cancel whose SELECT
    landed just before `confirm_email_change` committed stamped `cancelled_at`
    on an already-USED row and returned 1, telling someone their account was
    safe moments after the address had changed and their sessions were revoked.

    `.all()` appears nowhere in this function, in code or comment (checked), so
    unlike the upload-guard tests this substring cannot be satisfied by prose."""
    import inspect

    from app.services import email_change as ec

    src = inspect.getsource(ec.cancel_email_change)
    assert ".all()" not in src, "read-then-assign is back"
    assert src.count("update(EmailChangeToken)") == 2, (
        "both branches must write through a conditional UPDATE"
    )
    assert "EmailChangeToken.used_at.is_(None)" in src


def test_the_self_cancel_leaves_other_users_alone(db, make_user):
    """Behavioural control for the same change: an over-broad WHERE would
    cancel everybody's pending change, which is the failure mode a hand-written
    UPDATE invites."""
    from app.models.email_change_token import EmailChangeToken
    from app.services import email_change as ec
    from app.utils.crypto import sha256_hex
    from app.utils.timeutil import utc_now

    mine = make_user(email="u@test.local", role=UserRole.employee, password=PW)
    other = make_user(email="o@test.local", role=UserRole.employee, password=PW)
    for owner, tag in ((mine, "a"), (other, "b")):
        db.add(
            EmailChangeToken(
                user_id=owner.id, new_email=f"new-{tag}@test.local",
                new_token_hash=sha256_hex(tag),
                cancel_token_hash=sha256_hex(tag + "c"),
                expires_at=utc_now() + ec.EMAIL_CHANGE_TTL,
            )
        )
    db.commit()

    assert ec.cancel_email_change(db, user=mine, request=None) == 1
    db.commit()

    still_live = (
        db.query(EmailChangeToken)
        .filter(
            EmailChangeToken.user_id == other.id,
            EmailChangeToken.cancelled_at.is_(None),
        )
        .count()
    )
    assert still_live == 1, "cancelling one user's change cancelled another's"

    # And a second cancel finds nothing left to do.
    assert ec.cancel_email_change(db, user=mine, request=None) == 0


# --- P17: a non-ASCII fingerprint 500'd instead of 409ing ------------------


def test_a_non_ascii_fingerprint_is_refused_by_the_schema():
    """`secrets.compare_digest` raises TypeError on non-ASCII str operands, so
    the value reached it and produced a 500 from the one route whose whole job
    is to answer 409 CONTENT_CHANGED."""
    from pydantic import ValidationError

    from app.schemas.share import ApproveShareRequest, DecideAddedFilesRequest

    for model, extra in (
        (ApproveShareRequest, {}),
        (DecideAddedFilesRequest, {"approve": True}),
    ):
        with pytest.raises(ValidationError):
            model(content_fingerprint="ü" * 8, **extra)
        with pytest.raises(ValidationError):
            model(content_fingerprint="zzzz", **extra)  # hex-only
        # Positive control: the real producer format still validates.
        assert model(content_fingerprint="0a1b2c3d4e5f6789", **extra)


# --- L13: disabled legal pages were served anyway --------------------------


@pytest.mark.asyncio
async def test_a_disabled_legal_page_serves_no_content(db, client):
    """`enabled` was computed and then not used as a gate — the only thing
    honouring it was the SPA. So an unpublished draft imprint, which is where a
    company address or a not-yet-agreed legal position lives, was one anonymous
    curl away."""
    from app.services import settings as settings_svc

    settings_svc.set_value(
        db, key=settings_svc.Keys.LEGAL_IMPRINT_EN,
        value="<p>draft address</p>", actor=None,
    )
    settings_svc.set_value(
        db, key=settings_svc.Keys.LEGAL_IMPRINT_ENABLED, value="false", actor=None
    )
    db.commit()

    body = (await client.get("/api/legal/imprint")).json()
    assert body["enabled"] is False
    assert body["html_en"] == "", "a disabled page served its draft body"

    # Positive control: enabling it publishes, so this cannot pass by the route
    # simply being broken.
    settings_svc.set_value(
        db, key=settings_svc.Keys.LEGAL_IMPRINT_ENABLED, value="true", actor=None
    )
    db.commit()
    body = (await client.get("/api/legal/imprint")).json()
    assert body["enabled"] is True
    assert "draft address" in body["html_en"]


# --- L14: the SPA 404 beacon had no aggregate ceiling ----------------------


@pytest.mark.asyncio
async def test_the_404_beacon_has_a_global_ceiling(client, monkeypatch):
    """The per-IP cap bounded one source at 10/min and nothing bounded the sum,
    so N sources wrote error_log rows and ARQ jobs without limit. The CSP sink
    beside it had both caps and a comment claiming it was the only entry point
    that had lacked one."""
    from app.services import error_log, job_queue, rate_limit

    monkeypatch.setattr(error_log, "capture_4xx_enabled_cached", lambda: True)
    seen: list[str] = []

    def _buckets(bucket, ip, limit, window_sec=900):
        seen.append(bucket)
        return bucket != "client_404_global"  # the global one is exhausted

    monkeypatch.setattr(rate_limit, "check_ip_allowed", _buckets)
    enqueued: list = []
    monkeypatch.setattr(job_queue, "enqueue", lambda *a, **kw: enqueued.append(a))

    resp = await client.post("/api/telemetry/page-404", json={"path": "/nope"})
    assert resp.status_code == 204
    assert "client_404_global" in seen, "no global ceiling is consulted"
    assert enqueued == [], "the global ceiling did not shed the event"
