"""Stateless manage-subscriptions token (services/unsubscribe_token.py)."""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.services import unsubscribe_token as tok


def test_issue_verify_roundtrip():
    t = tok.issue(42)
    assert tok.verify(t) == 42


def test_format_is_four_dotted_parts():
    """`<uid>.<iat>.<exp>.<sig>`. The `iat` is what lets the caller compare the
    token against `users.sessions_invalidated_at`; without it a 180-day footer
    link survived every revocation the product offers."""
    t = tok.issue(7)
    parts = t.split(".")
    assert len(parts) == 4
    assert parts[0] == "7"
    assert int(parts[1]) <= int(parts[2])  # iat before exp


def test_verify_full_reports_the_issue_time():
    t = tok.issue(7)
    user_id, iat = tok.verify_full(t)
    assert user_id == 7
    assert isinstance(iat, int)


def test_tampered_signature_rejected():
    t = tok.issue(5)
    user, iat, exp, _sig = t.split(".", 3)
    forged = f"{user}.{iat}.{exp}.deadbeef"
    with pytest.raises(AppError) as ei:
        tok.verify(forged)
    assert ei.value.code == "INVALID_MANAGE_TOKEN"


def test_swapped_user_id_rejected():
    """Changing the embedded user id invalidates the signature."""
    t = tok.issue(5)
    _user, iat, exp, sig = t.split(".", 3)
    with pytest.raises(AppError):
        tok.verify(f"9.{iat}.{exp}.{sig}")


def test_forged_issue_time_rejected():
    """The `iat` is SIGNED. If it were not, a holder could backdate it to slip
    under a revocation mark - which is the whole point of carrying it."""
    t = tok.issue(5)
    user, iat, exp, sig = t.split(".", 3)
    with pytest.raises(AppError) as ei:
        tok.verify(f"{user}.{int(iat) - 10_000}.{exp}.{sig}")
    assert ei.value.code == "INVALID_MANAGE_TOKEN"


# --- legacy three-part tokens ----------------------------------------------
#
# Accepted on purpose: they are in mail already DELIVERED, and refusing them
# would break every Manage-subscriptions and RFC 8058 one-click link in flight.
# They drain within one TTL. sse_token refuses its old format instead, and can -
# its TTL is five minutes.


def _legacy(user_id: int, ttl_sec: int = 3600) -> str:
    exp = tok._now() + ttl_sec
    sig = tok._sign(f"notif-mgmt|{user_id}|{exp}".encode())
    return f"{user_id}.{exp}.{sig}"


def test_a_legacy_three_part_token_still_verifies():
    assert tok.verify(_legacy(11)) == 11


def test_a_legacy_token_reports_no_issue_time():
    """`None`, never 0. Reporting the epoch would make every legacy token look
    older than any revocation mark and lock the whole population out - the
    breakage this back-compat exists to avoid."""
    user_id, iat = tok.verify_full(_legacy(11))
    assert user_id == 11
    assert iat is None


def test_a_legacy_signature_is_not_accepted_in_the_new_shape():
    """The signed payload differs between the formats, so a three-part
    signature cannot be replayed as a four-part token."""
    user, exp, sig = _legacy(11).split(".", 2)
    with pytest.raises(AppError):
        tok.verify(f"{user}.{tok._now()}.{exp}.{sig}")


def test_an_expired_legacy_token_is_still_refused():
    with pytest.raises(AppError) as ei:
        tok.verify(_legacy(11, ttl_sec=-10))
    assert ei.value.code == "MANAGE_TOKEN_EXPIRED"


def test_expired_token_rejected():
    t = tok.issue(1, ttl_sec=-10)
    with pytest.raises(AppError) as ei:
        tok.verify(t)
    assert ei.value.code == "MANAGE_TOKEN_EXPIRED"


def test_malformed_token_rejected():
    with pytest.raises(AppError) as ei:
        tok.verify("not-a-token")
    assert ei.value.code == "INVALID_MANAGE_TOKEN"
