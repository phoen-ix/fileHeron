"""Signed SSE token: round-trip, default TTL, and rejection paths."""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.services import sse_token as t


def test_issue_verify_roundtrip():
    tok = t.issue(42)
    assert t.verify(tok) == 42


def test_verify_full_reports_the_issue_time():
    """The issue time is what lets the stream honour
    users.sessions_invalidated_at - without it a revoked session kept reading
    the event stream for the rest of the token's life."""
    before = t._now()
    user_id, iat = t.verify_full(t.issue(7))
    assert user_id == 7
    assert before <= iat <= t._now()


def test_default_ttl_is_five_minutes():
    assert t.DEFAULT_TTL_SEC == 300
    tok = t.issue(1)
    _user, _iat, exp, _sig = tok.split(".", 3)
    # exp ≈ now + 300 (allow a little slack for execution time)
    assert 290 <= int(exp) - t._now() <= 300


def test_expired_token_rejected():
    tok = t.issue(1, ttl_sec=-1)
    with pytest.raises(AppError) as exc:
        t.verify(tok)
    assert exc.value.status_code == 401


def test_tampered_signature_rejected():
    tok = t.issue(1)
    user, iat, exp, _sig = tok.split(".", 3)
    with pytest.raises(AppError):
        t.verify(f"{user}.{iat}.{exp}.deadbeef")


def test_a_forged_issue_time_is_rejected():
    """iat is inside the signed payload, so backdating it to slip past the
    revocation mark invalidates the signature."""
    tok = t.issue(1)
    user, iat, exp, sig = tok.split(".", 3)
    with pytest.raises(AppError):
        t.verify(f"{user}.{int(iat) - 3600}.{exp}.{sig}")


def test_the_old_three_part_format_is_refused():
    """Pre-2026-08 tokens carry no issue time, so they cannot be checked against
    the revocation mark. They 401 and the SPA re-mints; the whole population is
    at most one TTL old."""
    with pytest.raises(AppError):
        t.verify(f"1.{t._now() + 300}.whatever")
