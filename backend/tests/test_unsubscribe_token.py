"""Stateless manage-subscriptions token (services/unsubscribe_token.py)."""
from __future__ import annotations

import pytest

from app.middleware.errors import AppError
from app.services import unsubscribe_token as tok


def test_issue_verify_roundtrip():
    t = tok.issue(42)
    assert tok.verify(t) == 42


def test_format_is_three_dotted_parts():
    t = tok.issue(7)
    parts = t.split(".")
    assert len(parts) == 3
    assert parts[0] == "7"


def test_tampered_signature_rejected():
    t = tok.issue(5)
    user, exp, _sig = t.split(".", 2)
    forged = f"{user}.{exp}.deadbeef"
    with pytest.raises(AppError) as ei:
        tok.verify(forged)
    assert ei.value.code == "INVALID_MANAGE_TOKEN"


def test_swapped_user_id_rejected():
    """Changing the embedded user id invalidates the signature."""
    t = tok.issue(5)
    _user, exp, sig = t.split(".", 2)
    with pytest.raises(AppError):
        tok.verify(f"9.{exp}.{sig}")


def test_expired_token_rejected():
    t = tok.issue(1, ttl_sec=-10)
    with pytest.raises(AppError) as ei:
        tok.verify(t)
    assert ei.value.code == "MANAGE_TOKEN_EXPIRED"


def test_malformed_token_rejected():
    with pytest.raises(AppError) as ei:
        tok.verify("not-a-token")
    assert ei.value.code == "INVALID_MANAGE_TOKEN"
