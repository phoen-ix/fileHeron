"""Admin IMAP settings + inbox endpoints (v1.27.0)."""
from __future__ import annotations

import pytest

from app.models.inbound_attachment import AttachmentAVState, InboundAttachment
from app.models.inbound_message import InboundMessage, MessageClass, MessageStatus
from app.models.user import UserRole
from app.services import settings as s
from app.utils.timeutil import utc_now

PW = "Pass12345678!"


async def _admin_token(make_user, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    return (await login_as("admin@test.local", PW))[0]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


def _seed_msg(db, **kw):
    m = InboundMessage(
        sender_email=kw.get("sender", "x@example.com"),
        subject=kw.get("subject", "Hello"),
        imap_uid=kw.get("uid", 1),
        uidvalidity=1,
        classification=kw.get("cls", MessageClass.normal),
        status=kw.get("status", MessageStatus.new),
        has_attachments=kw.get("att", False),
        created_at=utc_now(),
    )
    db.add(m)
    db.commit()
    return m


@pytest.mark.asyncio
async def test_settings_get_default(make_user, client, login_as):
    t = await _admin_token(make_user, login_as)
    r = await client.get("/api/admin/settings/imap", headers=_h(t))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False
    assert body["is_password_set"] is False
    assert body["post_fetch_action"] == "mark_read"


@pytest.mark.asyncio
async def test_settings_reuse_smtp_credentials(make_user, db, client, login_as):
    t = await _admin_token(make_user, login_as)
    s.set_value(db, key=s.Keys.SMTP_PASSWORD, value="smtp-pass", actor=None)
    s.set_value(db, key=s.Keys.SMTP_USER, value="bot@example.com", actor=None)
    db.commit()
    r = await client.get("/api/admin/settings/imap", headers=_h(t))
    body = r.json()
    # Default on → reuses the SMTP login, so a password is effectively set.
    assert body["use_smtp_credentials"] is True
    assert body["is_password_set"] is True
    assert body["user"] == "bot@example.com"


@pytest.mark.asyncio
async def test_settings_put_masks_password(make_user, db, client, login_as):
    t = await _admin_token(make_user, login_as)
    r = await client.put(
        "/api/admin/settings/imap",
        json={
            "enabled": True, "use_smtp_credentials": False,
            "host": "imap.example.com",
            "port": 993, "user": "bot", "password": "s3cret",
            "tls_mode": "implicit", "mailbox": "INBOX",
            "post_fetch_action": "mark_read", "move_folder": "fileHeron/Processed",
            "notify_mode": "off",
        },
        headers=_h(t),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True and body["is_password_set"] is True
    assert "s3cret" not in r.text  # secret never echoed
    # stored encrypted
    assert s.get(db, s.Keys.IMAP_PASSWORD) == "s3cret"
    # null password on next save keeps it
    r2 = await client.put(
        "/api/admin/settings/imap",
        json={
            "enabled": True, "use_smtp_credentials": False,
            "host": "imap.example.com",
            "port": 993, "user": "bot", "password": None, "tls_mode": "implicit",
            "mailbox": "INBOX", "post_fetch_action": "mark_read",
            "move_folder": "fileHeron/Processed", "notify_mode": "off",
        },
        headers=_h(t),
    )
    assert r2.json()["is_password_set"] is True


@pytest.mark.asyncio
async def test_test_connection_endpoint(make_user, client, login_as, monkeypatch):
    t = await _admin_token(make_user, login_as)
    from app.services import imap_poll
    monkeypatch.setattr(
        imap_poll, "test_connection",
        lambda db, **k: {"ok": True, "error": None, "hint": None, "folders": ["INBOX"]},
    )
    r = await client.post("/api/admin/settings/imap/test", headers=_h(t))
    assert r.status_code == 200, r.text
    assert r.json()["folders"] == ["INBOX"]


@pytest.mark.asyncio
@pytest.mark.parametrize("n_messages", [0, 2])
async def test_fetch_now_answers_the_real_poll_result(
    n_messages, make_user, client, login_as, db, monkeypatch
):
    """The route must carry run_poll's REAL result, so only the mailbox is faked.

    This test used to replace run_poll with a hand-built dict that had no
    `skipped` key, so it never saw the success path's message count - which went
    out as `skipped`, an int, against a response model typing `skipped` as the
    reason a poll did not run. Every successful manual fetch answered 500
    (error_log, 2026-09-19)."""
    import functools

    from app.services import imap_poll
    from tests.test_inbound_mail import NORMAL, _FakeSession, _opener

    t = await _admin_token(make_user, login_as)
    s.set_value(db, key=s.Keys.IMAP_ENABLED, value="true", actor=None)
    s.set_value(db, key=s.Keys.IMAP_HOST, value="imap.example.com", actor=None)
    s.set_value(db, key=s.Keys.IMAP_REQUIRE_KNOWN_SENDER, value="false", actor=None)
    db.commit()
    msgs = {
        uid: NORMAL.replace(b"<n1@example.com>", f"<n{uid}@example.com>".encode())
        for uid in range(1, n_messages + 1)
    }
    monkeypatch.setattr(
        imap_poll,
        "run_poll",
        functools.partial(imap_poll.run_poll, session_opener=_opener(_FakeSession(msgs))),
    )

    r = await client.post("/api/admin/settings/imap/fetch-now", headers=_h(t))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["skipped"] is None  # the poll ran; nothing to report as a reason
    assert body["skipped_messages"] == 0
    assert body["ingested"] == n_messages


@pytest.mark.asyncio
async def test_inbox_list_and_filters(make_user, db, client, login_as):
    t = await _admin_token(make_user, login_as)
    _seed_msg(db, uid=1, subject="Re: files", cls=MessageClass.normal)
    _seed_msg(db, uid=2, subject="Delivery failed", cls=MessageClass.bounce)
    r = await client.get("/api/admin/inbox", headers=_h(t))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2 and body["unread"] == 2
    r2 = await client.get("/api/admin/inbox?classification=bounce", headers=_h(t))
    assert r2.json()["total"] == 1


@pytest.mark.asyncio
async def test_inbox_detail_and_status(make_user, db, client, login_as):
    t = await _admin_token(make_user, login_as)
    m = _seed_msg(db, uid=1)
    r = await client.get(f"/api/admin/inbox/{m.id}", headers=_h(t))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "new"
    r2 = await client.patch(f"/api/admin/inbox/{m.id}", json={"status": "read"}, headers=_h(t))
    assert r2.status_code == 200 and r2.json()["status"] == "read"


@pytest.mark.asyncio
async def test_attachment_download_gated_on_av(make_user, db, client, login_as):
    t = await _admin_token(make_user, login_as)
    m = _seed_msg(db, uid=1, att=True)
    pending = InboundAttachment(
        message_id=m.id, filename="x.pdf", content_type="application/pdf",
        size_bytes=10, storage_key="/nope", av_state=AttachmentAVState.pending,
    )
    db.add(pending)
    db.commit()
    r = await client.get(
        f"/api/admin/inbox/{m.id}/attachments/{pending.id}/download", headers=_h(t)
    )
    assert r.status_code == 409  # not clean → refused


@pytest.mark.asyncio
async def test_non_admin_forbidden(make_user, client, login_as):
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    t = (await login_as("emp@test.local", PW))[0]
    assert (await client.get("/api/admin/inbox", headers=_h(t))).status_code in (401, 403)
    assert (await client.get("/api/admin/settings/imap", headers=_h(t))).status_code in (401, 403)
