"""A `%` or `_` typed into a search box matches itself, everywhere.

Seven list endpoints build `ilike(f"%{q}%")`. Four escaped the LIKE
metacharacters with their own copy of three `replace` calls; the admin
mail-log, inbox and session searches did not, so `_` - common in email
addresses - matched any character and `%` matched everything. One helper now
(`utils/like.py`), and these pin the three that had nothing.
"""
from __future__ import annotations

import pytest

from app.models.email_log import EmailLog, EmailStatus, EmailVia
from app.models.user import UserRole
from app.utils.like import LIKE_ESCAPE, contains, escape_like
from app.utils.timeutil import utc_now

PW = "Pass12345678!"


def test_escape_like_escapes_the_three_metacharacters():
    assert LIKE_ESCAPE == "\\"
    assert escape_like("50%_off\\") == "50\\%\\_off\\\\"
    assert contains("a_b") == "%a\\_b%"
    assert escape_like("plain") == "plain"


async def _admin_headers(make_user, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    token, _ = await login_as("admin@test.local", PW)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_mail_log_search_treats_wildcards_literally(make_user, db, client, login_as):
    h = await _admin_headers(make_user, login_as)
    for subject in ("100% done", "plain subject"):
        db.add(
            EmailLog(
                recipient_email="x@test.local",
                subject=subject,
                category="share_created",
                status=EmailStatus.sent,
                via=EmailVia.direct,
                created_at=utc_now(),
            )
        )
    db.commit()

    r = await client.get("/api/admin/mail-log", params={"q": "%"}, headers=h)
    assert r.status_code == 200, r.text
    assert [i["subject"] for i in r.json()["items"]] == ["100% done"]

    # No subject or recipient carries an underscore: unescaped, `_` matched
    # any character and returned both rows.
    r = await client.get("/api/admin/mail-log", params={"q": "_"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_inbox_search_treats_wildcards_literally(make_user, db, client, login_as):
    from app.models.inbound_message import InboundMessage, MessageClass, MessageStatus

    h = await _admin_headers(make_user, login_as)
    for uid, subject in ((1, "quarterly_figures"), (2, "plain subject")):
        db.add(
            InboundMessage(
                sender_email="sender@example.com",
                subject=subject,
                imap_uid=uid,
                uidvalidity=1,
                classification=MessageClass.normal,
                status=MessageStatus.new,
                has_attachments=False,
                created_at=utc_now(),
            )
        )
    db.commit()

    r = await client.get("/api/admin/inbox", params={"q": "_"}, headers=h)
    assert r.status_code == 200, r.text
    assert [i["subject"] for i in r.json()["items"]] == ["quarterly_figures"]

    r = await client.get("/api/admin/inbox", params={"q": "%"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_session_search_treats_wildcards_literally(make_user, client, login_as):
    """The admin's own login just created a session row. Neither its email,
    display name nor created_ip contains `%`, so a literal `%` search must find
    nothing - unescaped it matched every session on the instance."""
    h = await _admin_headers(make_user, login_as)

    r = await client.get("/api/admin/sessions", params={"q": "%"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0

    r = await client.get("/api/admin/sessions", params={"q": "admin@test"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["total"] >= 1
