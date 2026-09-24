"""A lookup by email must be EXACT, and mail must go to the stored address.

`users.email` was `utf8mb4_unicode_ci` in production - case- and
accent-insensitive - so `victim@exämple.com` found `victim@example.com`, and
forgot-password mailed that account's reset link to the address the caller
typed: a lookalike IDN domain anyone can register. SQLite compares TEXT in
binary, so nothing in this suite could see it; the collation half is pinned in
test_mariadb_semantics.py, these pin the application half, which holds whatever
collation the column carries.
"""
from __future__ import annotations

import pytest

from app.models.user import User
from app.services.user_lookup import user_by_email, user_id_by_email


class _Query:
    def __init__(self, row):
        self._row = row

    def filter(self, *_a, **_k):
        return self

    def one_or_none(self):
        return self._row


class _CollationSession:
    """Answers every lookup with `row`, the way an accent-insensitive column
    answers `kévin@exämple.com` with the `kevin@example.com` row."""

    def __init__(self, row):
        self._row = row

    def query(self, *_a):
        return _Query(self._row)


def test_a_collation_match_is_not_a_match():
    row = User(id=7, email="kevin@example.com")
    db = _CollationSession(row)

    assert user_by_email(db, "kévin@exämple.com") is None
    assert user_id_by_email(db, "kevin@exämple.com") is None
    # Control: the same address, as a person types it, still resolves.
    assert user_by_email(db, "  Kevin@Example.com ") is row
    assert user_id_by_email(db, "kevin@example.com") == 7


@pytest.mark.asyncio
async def test_forgot_password_mails_the_stored_address_not_the_typed_one(
    make_user, client, monkeypatch
):
    user = make_user(email="alice@test.local")
    import app.routers.auth as auth_router

    # Stand in for the database matching a lookalike to this account - what
    # utf8mb4_unicode_ci did - and record where the link would be sent.
    monkeypatch.setattr(
        auth_router.auth_svc, "begin_password_reset",
        lambda db, *, email, request: (user, "plaintext-token"),
    )
    sent: list[str] = []

    async def _record(**kwargs):
        sent.append(kwargs["to"])

    monkeypatch.setattr(auth_router, "_send_password_reset_detached", _record)

    resp = await client.post(
        "/api/auth/forgot-password", json={"email": "alice@lookalike.test"}
    )
    assert resp.status_code == 200
    assert sent == ["alice@test.local"]
