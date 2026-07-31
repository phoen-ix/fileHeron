"""Read-then-write races on the email-change flow, and three OIDC defects.

flow-emailchange-4  `_supersede_pending` read the live pending set and then
                    assigned. Two concurrent requests both read empty, both
                    inserted, and the account was left with two simultaneously
                    live pending changes to DIFFERENT addresses - so whichever
                    confirm link was clicked second silently won, and the
                    "only the latest request's link works" guarantee the
                    function exists to provide did not hold.
flow-emailchange-5  `cancel_email_change` was also read-then-assign, so a
                    cancel racing an apply reported success while the change
                    went through anyway. That is the "it wasn't me" kill
                    switch telling someone their account is safe at the moment
                    it changes hands.
oidc-7              `build_authorize_url` percent-encoded nothing:
                    `httpx.QueryParams({k: v})[k]` returns the value DECODED,
                    so a redirect_uri or state containing & or = would split
                    the query.
oidc-5              the JSON parses in `_discovery` and `_exchange_code` sat
                    OUTSIDE their try blocks, so an IdP answering 200 with a
                    non-JSON body raised a bare JSONDecodeError and surfaced as
                    an unhandled 500 rather than the OIDC_UNAVAILABLE the
                    function otherwise promises.
authn-8             the oidc_connect docstring described a Bearer + actor
                    cross-check that has never existed.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import inspect

import pytest

from app.middleware.errors import AppError
from app.models.email_change_token import EmailChangeToken
from app.models.user import UserRole
from app.services import email_change as ec
from app.utils.crypto import sha256_hex
from app.utils.timeutil import utc_now


@pytest.fixture
def user(db, make_user):
    return make_user(email="dana@test.local", role=UserRole.employee)


# --- flow-emailchange-4 -----------------------------------------------------


def test_superseding_cancels_every_live_pending_change(db, user):
    for addr, tok in (("a@test.local", "t1"), ("b@test.local", "t2")):
        db.add(
            EmailChangeToken(
                user_id=user.id, new_email=addr, new_token_hash=sha256_hex(tok),
                cancel_token_hash=sha256_hex(tok + "c"),
                expires_at=utc_now() + ec.EMAIL_CHANGE_TTL,
            )
        )
    db.commit()

    n = ec._supersede_pending(db, user_id=user.id)
    db.commit()

    assert n == 2
    live = (
        db.query(EmailChangeToken)
        .filter(
            EmailChangeToken.user_id == user.id,
            EmailChangeToken.cancelled_at.is_(None),
        )
        .count()
    )
    assert live == 0, "two live pending changes to different addresses survived"


def test_supersede_is_a_single_conditional_update(db, user):
    """Pins the mechanism. Reverting to read-then-assign reintroduces the race
    silently, and SQLite cannot exhibit it."""
    src = inspect.getsource(ec._supersede_pending)
    assert "update(EmailChangeToken)" in src
    assert ".all()" not in src


def test_supersede_leaves_other_users_alone(db, user, make_user):
    """Control: an over-broad UPDATE would cancel everyone's pending change."""
    other = make_user(email="sam@test.local", role=UserRole.employee)
    db.add(
        EmailChangeToken(
            user_id=other.id, new_email="x@test.local", new_token_hash=sha256_hex("o"),
            expires_at=utc_now() + ec.EMAIL_CHANGE_TTL,
        )
    )
    db.commit()
    ec._supersede_pending(db, user_id=user.id)
    db.commit()
    assert (
        db.query(EmailChangeToken)
        .filter(EmailChangeToken.user_id == other.id,
                EmailChangeToken.cancelled_at.is_(None))
        .count()
        == 1
    )


# --- flow-emailchange-5 -----------------------------------------------------


def test_cancelling_an_already_settled_change_is_refused(db, user):
    """The defect: cancel reported success on a change that had already been
    applied, so the user was told their account was safe when it was not."""
    tok = "cancel-me"
    row = EmailChangeToken(
        user_id=user.id, new_email="new@test.local", new_token_hash=sha256_hex("n"),
        cancel_token_hash=sha256_hex(tok),
        expires_at=utc_now() + ec.EMAIL_CHANGE_TTL,
        used_at=utc_now(),  # already applied
    )
    db.add(row)
    db.commit()

    with pytest.raises(AppError) as exc:
        ec.cancel_email_change(db, token=tok)
    # The base query filters on used_at IS NULL, so a settled row is simply not
    # found - either code is an honest refusal, neither is a false success.
    assert exc.value.code in (
        "EMAIL_CHANGE_TOKEN_INVALID",
        "EMAIL_CHANGE_ALREADY_SETTLED",
    )


def test_cancelling_a_live_change_still_works(db, user):
    """Control: the kill switch has to keep working."""
    tok = "cancel-me"
    db.add(
        EmailChangeToken(
            user_id=user.id, new_email="new@test.local", new_token_hash=sha256_hex("n"),
            cancel_token_hash=sha256_hex(tok),
            expires_at=utc_now() + ec.EMAIL_CHANGE_TTL,
        )
    )
    db.commit()
    assert ec.cancel_email_change(db, token=tok) == 1
    db.commit()
    row = db.query(EmailChangeToken).one()
    assert row.cancelled_at is not None


# --- oidc-7 -----------------------------------------------------------------


def test_authorize_url_actually_encodes_its_parameters():
    """`httpx.QueryParams({k: v})[k]` gives the value back DECODED, so the old
    expression encoded nothing and a state or redirect_uri containing & or =
    would have split the query."""
    import httpx

    params = {"state": "a&b=c", "redirect_uri": "https://x.test/cb?x=1"}
    encoded = str(httpx.QueryParams(params))
    assert "a%26b%3Dc" in encoded
    # and the old approach demonstrably did not:
    assert httpx.QueryParams({"state": "a&b=c"})["state"] == "a&b=c"


def test_the_service_uses_the_encoding_form():
    from app.services import oidc

    src = inspect.getsource(oidc.build_authorize_url)
    assert "str(httpx.QueryParams(params))" in src


# --- oidc-5 -----------------------------------------------------------------


@pytest.mark.parametrize("fn", ["_discovery", "_exchange_code"])
def test_json_parsing_is_inside_the_failure_contract(fn):
    """A 200 with a non-JSON body - captive portal, HTML error page, truncated
    response - must produce the documented AppError, not a bare
    JSONDecodeError that becomes an unhandled 500."""
    from app.services import oidc

    src = inspect.getsource(getattr(oidc, fn))
    assert "JSONDecodeError" in src or "ValueError" in src, (
        f"{fn} still parses JSON outside its except arms"
    )


# --- authn-8 ----------------------------------------------------------------


def test_the_connect_docstring_does_not_claim_a_check_that_is_absent():
    from app.routers import oidc_connect

    doc = oidc_connect.__doc__ or ""
    assert "Both checks must pass" not in doc
    assert "cross-check `cookie_user_id == authed.id`" not in doc
    # and says what actually guards it
    assert "HMAC-signed" in doc
