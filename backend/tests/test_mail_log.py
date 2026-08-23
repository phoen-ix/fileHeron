"""Mail log (v1.11.0) - masking, queued/finalize lifecycle, dispatch wiring,
resend gating, erasure scrub, and retention pruning."""
from __future__ import annotations

from datetime import timedelta

import pytest
from aiosmtplib.errors import SMTPConnectError, SMTPResponseException
from arq import Retry
from sqlalchemy import inspect as sa_inspect

from app.models.email_log import EmailLog, EmailStatus, EmailVia
from app.models.user import UserRole
from app.services import mail_log
from app.utils.emailing import SmtpConfig
from app.utils.timeutil import utc_now
from app.workers import send_email as worker_mod

_CFG = SmtpConfig(
    host="smtp.example.com",
    port=587,
    user="u",
    password="p",
    from_email="f@example.com",
    from_name="F",
    tls_mode="starttls",
)


# --- masking ---------------------------------------------------------------


def test_mask_redacts_each_auth_link():
    for path in (
        "reset-password",
        "verify-email",
        "register",
        "confirm-email-change",
        "cancel-email-change",
    ):
        text = f"Hello, visit https://x.test/{path}/abc.def-123_TOK to continue."
        out, redacted = mail_log.mask_sensitive(text)
        assert redacted is True
        assert f"/{path}/<redacted>" in out
        assert "abc.def-123_TOK" not in out


def test_mask_bodies_email_change_categories_force_masked():
    # A token-free body still masks because the category is a known
    # token-bearer (resend stays disabled regardless).
    for cat in ("email_change_confirm", "email_change_verify_old", "email_change_alert"):
        _t, _h, masked = mail_log.mask_bodies("hi", "<p>hi</p>", cat)
        assert masked is True, cat
    # The completion notice is token-free → not forced masked.
    _t, _h, masked = mail_log.mask_bodies("hi", "<p>hi</p>", "email_change_completed")
    assert masked is False


def test_mask_leaves_ordinary_text_untouched():
    out, redacted = mail_log.mask_sensitive("A normal share notice. No tokens here.")
    assert redacted is False
    assert out == "A normal share notice. No tokens here."
    assert mail_log.mask_sensitive(None) == (None, False)


def test_mask_bodies_category_forces_masked():
    # No token in the body, but the category is a known token-bearer.
    _t, _h, masked = mail_log.mask_bodies("hi", "<p>hi</p>", "invite")
    assert masked is True


def test_mask_fails_closed(monkeypatch):
    class _BadRe:
        def subn(self, *_a, **_kw):
            raise RuntimeError("boom")

    monkeypatch.setattr(mail_log, "_AUTH_LINK_RE", _BadRe())
    out, redacted = mail_log.mask_sensitive("anything /reset-password/tok")
    assert redacted is True
    assert "tok" not in out  # body dropped to the placeholder, no live token kept


# --- record_queued + finalize lifecycle ------------------------------------


def test_record_queued_then_finalize_single_row(db):
    eid = mail_log.record_queued(
        db,
        recipient_email="a@b.c",
        recipient_user_id=None,
        category="share_created",
        template_slug="share_created",
        subject="S",
        text_body="hello",
        html_body=None,
    )
    db.flush()
    row = db.get(EmailLog, eid)
    assert row.status == EmailStatus.queued
    assert row.masked is False

    mail_log.finalize(db, email_log_id=eid, status=EmailStatus.queued, attempt=1)
    mail_log.finalize(db, email_log_id=eid, status=EmailStatus.sent, attempt=2)
    db.flush()
    assert db.query(EmailLog).count() == 1  # one row, updated in place
    assert row.status == EmailStatus.sent
    assert row.attempts == 2


def test_finalize_missing_row_is_noop(db):
    # No raise even though the row never existed (rollback-orphan case).
    mail_log.finalize(db, email_log_id=999999, status=EmailStatus.sent, attempt=1)


def test_list_query_defers_bodies(db):
    db.add(
        EmailLog(
            recipient_email="a@b.c",
            category="x",
            via=EmailVia.queued,
            status=EmailStatus.sent,
            subject="s",
            body_text="BIG BODY",
            masked=False,
            attempts=1,
        )
    )
    db.commit()
    db.expire_all()
    row = db.query(EmailLog).first()
    unloaded = sa_inspect(row).unloaded
    assert "body_text" in unloaded and "body_html" in unloaded
    assert row.body_text == "BIG BODY"  # accessing the detail body loads it
    assert "body_text" not in sa_inspect(row).unloaded


# --- worker finalize at each terminal branch -------------------------------


@pytest.mark.asyncio
async def test_worker_finalizes_sent(db, monkeypatch):
    eid = mail_log.record_queued(
        db, recipient_email="a@b.c", recipient_user_id=None,
        category="share_created", template_slug="share_created",
        subject="S", text_body="hi", html_body=None,
    )
    db.commit()

    async def _fake(**_kw):
        return None

    monkeypatch.setattr(worker_mod, "send_email", _fake)
    monkeypatch.setattr(worker_mod, "resolve_smtp_config", lambda _db: _CFG)
    await worker_mod.send_email_job(
        {"job_try": 1}, to="a@b.c", subject="S", text_body="hi", email_log_id=eid
    )
    db.expire_all()
    row = db.get(EmailLog, eid)
    assert row.status == EmailStatus.sent
    assert row.attempts == 1


@pytest.mark.asyncio
async def test_worker_finalizes_failed_5xx(db, monkeypatch):
    eid = mail_log.record_queued(
        db, recipient_email="bad@b.c", recipient_user_id=None,
        category="share_created", template_slug="share_created",
        subject="S", text_body="hi", html_body=None,
    )
    db.commit()

    async def _fake(**_kw):
        raise SMTPResponseException(550, "no such mailbox")

    monkeypatch.setattr(worker_mod, "send_email", _fake)
    monkeypatch.setattr(worker_mod, "resolve_smtp_config", lambda _db: _CFG)
    res = await worker_mod.send_email_job(
        {"job_try": 1}, to="bad@b.c", subject="S", text_body="hi", email_log_id=eid
    )
    assert res["status"] == "failed"
    db.expire_all()
    row = db.get(EmailLog, eid)
    assert row.status == EmailStatus.failed
    assert row.smtp_code == 550


@pytest.mark.asyncio
async def test_worker_transient_keeps_one_row_attempts_climb(db, monkeypatch):
    eid = mail_log.record_queued(
        db, recipient_email="x@b.c", recipient_user_id=None,
        category="share_created", template_slug="share_created",
        subject="S", text_body="hi", html_body=None,
    )
    db.commit()

    async def _fake(**_kw):
        raise SMTPConnectError("connection refused")

    monkeypatch.setattr(worker_mod, "send_email", _fake)
    monkeypatch.setattr(worker_mod, "resolve_smtp_config", lambda _db: _CFG)
    for attempt in (1, 2):
        with pytest.raises(Retry):
            await worker_mod.send_email_job(
                {"job_try": attempt}, to="x@b.c", subject="S",
                text_body="hi", email_log_id=eid,
            )
    db.expire_all()
    rows = db.query(EmailLog).all()
    assert len(rows) == 1
    assert rows[0].status == EmailStatus.queued
    assert rows[0].attempts == 2


# --- dispatch wiring (queued path) -----------------------------------------


def test_dispatch_logs_queued_and_enqueues_same_body(db, make_user, monkeypatch):
    from app.models.notification import NotificationCategory
    from app.services import notification as notif

    captured: dict = {}

    def _enq(name, **kw):
        captured["name"] = name
        captured.update(kw)

    monkeypatch.setattr(notif.job_queue, "enqueue", _enq)
    monkeypatch.setattr(
        notif.email_svc, "render_email",
        lambda *_a, **_k: ("Subj", "text body", "<p>html</p>"),
    )
    u = make_user(email="r@test.local")
    notif.dispatch(
        db, user=u, category=NotificationCategory.share_created,
        payload={}, email_to=u.email,
    )
    db.commit()

    rows = db.query(EmailLog).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == EmailStatus.queued
    assert row.subject == "Subj"
    assert row.recipient_user_id == u.id
    # the SAME rendered body is both logged and enqueued, keyed by id
    assert captured["email_log_id"] == row.id
    assert captured["text_body"] == "text body"
    assert captured["to"] == u.email


# --- resend endpoint -------------------------------------------------------


@pytest.mark.asyncio
async def test_resend_creates_new_row(db, make_user, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    row = EmailLog(
        recipient_email="r@x.com", recipient_user_id=None,
        category="share_created", template_slug="share_created",
        via=EmailVia.queued, status=EmailStatus.sent,
        subject="Hello", body_text="body", body_html=None,
        masked=False, attempts=1,
    )
    db.add(row)
    db.commit()
    rid = row.id

    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.post(
        f"/api/admin/mail-log/{rid}/resend",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    new_id = resp.json()["new_log_id"]
    db.expire_all()
    new = db.get(EmailLog, new_id)
    assert new.via == EmailVia.resend
    assert new.source_log_id == rid
    assert new.recipient_email == "r@x.com"


@pytest.mark.asyncio
async def test_resend_masked_refused(db, make_user, client, login_as):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    row = EmailLog(
        recipient_email="r@x.com", recipient_user_id=None,
        category="reset_password", template_slug="reset_password",
        via=EmailVia.direct, status=EmailStatus.sent,
        subject="Reset", body_text="link", masked=True, attempts=1,
    )
    db.add(row)
    db.commit()
    rid = row.id

    token, _ = await login_as("admin@test.local", "Pass12345678!")
    resp = await client.post(
        f"/api/admin/mail-log/{rid}/resend",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "MAIL_RESEND_MASKED"


@pytest.mark.asyncio
async def test_mail_detail_returns_body_and_csv_route_not_shadowed(
    db, make_user, client, login_as
):
    make_user(email="admin@test.local", role=UserRole.admin, password="Pass12345678!")
    row = EmailLog(
        recipient_email="r@x.com", category="share_created",
        template_slug="share_created", via=EmailVia.queued, status=EmailStatus.sent,
        subject="Hello", body_text="the full body", body_html="<p>hi</p>",
        masked=False, attempts=1,
    )
    db.add(row)
    db.commit()
    rid = row.id
    token, _ = await login_as("admin@test.local", "Pass12345678!")
    hdr = {"Authorization": f"Bearer {token}"}

    detail = await client.get(f"/api/admin/mail-log/{rid}", headers=hdr)
    assert detail.status_code == 200, detail.text
    assert detail.json()["body_text"] == "the full body"
    assert detail.json()["can_resend"] is True

    # /export.csv must hit the CSV endpoint, not the int detail route.
    csv = await client.get("/api/admin/mail-log/export.csv", headers=hdr)
    assert csv.status_code == 200, csv.text
    assert csv.headers["content-type"].startswith("text/csv")
    assert "recipient_email" in csv.text  # header row present
    assert "the full body" not in csv.text  # bodies never exported


@pytest.mark.asyncio
async def test_mail_log_list_admin_only(db, make_user, client, login_as):
    make_user(email="c@test.local", role=UserRole.client, password="Pass12345678!")
    token, _ = await login_as("c@test.local", "Pass12345678!")
    resp = await client.get(
        "/api/admin/mail-log", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


# --- GDPR erasure scrub ----------------------------------------------------


def test_erasure_scrubs_mail_log(db, make_user):
    from app.services import erasure

    admin = make_user(email="admin@test.local", role=UserRole.admin)
    target = make_user(email="victim@test.local")
    row = EmailLog(
        recipient_email="victim@test.local", recipient_user_id=target.id,
        category="reset_password", template_slug="reset_password",
        via=EmailVia.direct, status=EmailStatus.sent,
        subject="Reset your password", body_text="secret link",
        masked=True, attempts=1,
    )
    db.add(row)
    db.commit()
    rid = row.id

    erasure.erase_user(db, actor=admin, target=target)
    db.commit()
    db.expire_all()
    r = db.get(EmailLog, rid)
    assert r.recipient_user_id is None
    assert r.recipient_email.endswith("@erased.invalid")
    assert r.body_text is None
    assert r.subject == "[erased]"


# --- retention prune -------------------------------------------------------


@pytest.mark.asyncio
async def test_prune_email_log(db):
    from app.workers.prune_history import _prune_table

    db.add_all(
        [
            EmailLog(
                recipient_email="old@b.c", category="x", via=EmailVia.queued,
                status=EmailStatus.sent, subject="s", masked=False, attempts=1,
                created_at=utc_now() - timedelta(days=200),
            ),
            EmailLog(
                recipient_email="new@b.c", category="x", via=EmailVia.queued,
                status=EmailStatus.sent, subject="s", masked=False, attempts=1,
            ),
        ]
    )
    db.commit()

    n = await _prune_table("email_log", 90, EmailLog.created_at, EmailLog)
    assert n == 1
    db.expire_all()
    remaining = db.query(EmailLog).all()
    assert len(remaining) == 1
    assert remaining[0].recipient_email == "new@b.c"

    # 0 disables pruning.
    assert await _prune_table("email_log", 0, EmailLog.created_at, EmailLog) == 0


# --- ARQ redelivery ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_redelivered_job_does_not_send_the_email_twice(db, monkeypatch):
    """ARQ is at-least-once and `max_tries` is 5. `send_email_job` sends BEFORE
    it records anything, so a redelivery of a job whose SMTP call already
    succeeded sent the mail again - and the in-app updater restarts the worker
    on every update, which is exactly what produces such a redelivery."""
    from app.workers import send_email as mod

    eid = mail_log.record_queued(
        db, recipient_email="dup@test.local", recipient_user_id=None,
        category="share_created", template_slug="share_created",
        subject="s", text_body="b", html_body=None,
    )
    mail_log.finalize(db, email_log_id=eid, status=EmailStatus.sent, attempt=1)
    db.commit()

    sent: list[str] = []

    async def _spy(**kw):
        sent.append(kw["to"])

    monkeypatch.setattr(mod, "send_email", _spy)
    out = await mod.send_email_job(
        {"job_try": 2}, to="dup@test.local", subject="s",
        text_body="b", email_log_id=eid,
    )
    assert out["status"] == "already_sent"
    assert sent == [], "the redelivered job sent the email again"


@pytest.mark.asyncio
async def test_a_queued_row_is_still_sent(db, monkeypatch):
    """The control. Without it the guard above is satisfied by a job that never
    sends anything at all."""
    from app.workers import send_email as mod

    eid = mail_log.record_queued(
        db, recipient_email="fresh@test.local", recipient_user_id=None,
        category="share_created", template_slug="share_created",
        subject="s", text_body="b", html_body=None,
    )
    db.commit()

    sent: list[str] = []

    async def _spy(**kw):
        sent.append(kw["to"])

    monkeypatch.setattr(mod, "send_email", _spy)
    out = await mod.send_email_job(
        {"job_try": 1}, to="fresh@test.local", subject="s",
        text_body="b", email_log_id=eid,
    )
    assert out["status"] == "sent"
    assert sent == ["fresh@test.local"]


@pytest.mark.asyncio
async def test_a_job_with_no_log_row_is_unaffected(db, monkeypatch):
    """Auth-flow mail sends `direct` with no email_log_id; the guard must not
    turn that into a silent no-op."""
    from app.workers import send_email as mod

    sent: list[str] = []

    async def _spy(**kw):
        sent.append(kw["to"])

    monkeypatch.setattr(mod, "send_email", _spy)
    out = await mod.send_email_job(
        {"job_try": 1}, to="direct@test.local", subject="s", text_body="b",
    )
    assert out["status"] == "sent"
    assert sent == ["direct@test.local"]
