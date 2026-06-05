"""Signed SSE token: round-trip, default TTL, and rejection paths."""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.services import sse_token as t


def test_issue_verify_roundtrip():
    tok = t.issue(42)
    assert t.verify(tok) == 42


def test_default_ttl_is_five_minutes():
    assert t.DEFAULT_TTL_SEC == 300
    tok = t.issue(1)
    _user, exp, _sig = tok.split(".", 2)
    # exp ≈ now + 300 (allow a little slack for execution time)
    assert 290 <= int(exp) - t._now() <= 300


def test_expired_token_rejected():
    tok = t.issue(1, ttl_sec=-1)
    with pytest.raises(AppError) as exc:
        t.verify(tok)
    assert exc.value.status_code == 401


def test_tampered_signature_rejected():
    tok = t.issue(1)
    user, exp, _sig = tok.split(".", 2)
    with pytest.raises(AppError):
        t.verify(f"{user}.{exp}.deadbeef")
