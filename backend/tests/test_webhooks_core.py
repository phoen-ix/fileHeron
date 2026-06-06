"""Outbound webhooks - emit fan-out, signing, delivery worker, audit hook."""
from __future__ import annotations

import hashlib
import hmac

import pytest

from app.models.webhook import Webhook, WebhookDelivery, WebhookDeliveryStatus
from app.services import webhook as webhook_svc
from app.utils.crypto import encrypt_setting


def _mk_webhook(db, *, events, active=True, url="https://example.test/hook", secret="s3cret"):
    wh = Webhook(
        name="wh",
        url=url,
        secret_encrypted=encrypt_setting(secret),
        event_types=events,
        active=active,
    )
    db.add(wh)
    db.commit()
    return wh


# ---- signing ---------------------------------------------------------------


def test_sign_matches_independent_hmac():
    sig = webhook_svc.sign("topsecret", b'{"a":1}')
    expected = hmac.new(b"topsecret", b'{"a":1}', hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected}"


# ---- emit ------------------------------------------------------------------


def test_emit_enqueues_for_subscribed_active_only(db, monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.job_queue.enqueue", lambda *a, **kw: calls.append(a))

    sub = _mk_webhook(db, events=["share_created", "file_quarantined"])
    _mk_webhook(db, events=["share_downloaded"])  # not subscribed to share_created
    _mk_webhook(db, events=["share_created"], active=False)  # inactive
    wildcard = _mk_webhook(db, events=["*"])  # subscribes to everything

    n = webhook_svc.emit(db, "share_created", {"target_id": "abc"})
    assert n == 2  # the subscribed one + the wildcard
    enqueued_ids = {a[1] for a in calls}
    assert enqueued_ids == {sub.id, wildcard.id}


def test_emit_never_raises(db, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.services.job_queue.enqueue", boom)
    _mk_webhook(db, events=["*"])
    # Must swallow the enqueue error - an action must never break on a webhook.
    assert webhook_svc.emit(db, "share_created", {}) == 0


def test_is_webhook_event():
    assert webhook_svc.is_webhook_event("share_created")
    assert webhook_svc.is_webhook_event("ops.alert")
    assert not webhook_svc.is_webhook_event("login_success")


# ---- audit hook ------------------------------------------------------------


def test_record_audit_event_emits_for_allowlisted_only(db, monkeypatch):
    emitted = []
    monkeypatch.setattr(webhook_svc, "emit", lambda _db, et, payload: emitted.append(et))

    from app.models.audit_log import AuditEventType
    from app.services.audit import record_audit_event

    record_audit_event(db, event_type=AuditEventType.share_created, target_type="share", target_id="s1")
    record_audit_event(db, event_type=AuditEventType.login_success, target_type="user", target_id="1")
    db.commit()

    assert "share_created" in emitted
    assert "login_success" not in emitted


# ---- delivery worker -------------------------------------------------------


class _FakeResp:
    def __init__(self, code):
        self.status_code = code


class _FakeClient:
    def __init__(self, code=None, raise_exc=None):
        self._code = code
        self._raise = raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, content=None, headers=None):
        if self._raise:
            raise self._raise
        return _FakeResp(self._code)


@pytest.mark.asyncio
async def test_webhook_deliver_success(db, monkeypatch):
    from app.workers import webhook_deliver as wd

    wh = _mk_webhook(db, events=["share_created"], secret="abc123")
    monkeypatch.setattr(wd.httpx, "AsyncClient", lambda *a, **kw: _FakeClient(code=200))

    res = await wd.webhook_deliver(None, wh.id, "share_created", {"target_id": "x"})
    assert res["status"] == "sent"

    row = db.query(WebhookDelivery).filter(WebhookDelivery.webhook_id == wh.id).one()
    assert row.status == WebhookDeliveryStatus.sent
    assert row.response_code == 200
    assert row.attempts == 1
    assert row.delivered_at is not None


@pytest.mark.asyncio
async def test_webhook_deliver_retry_then_fail(db, monkeypatch):
    from app.workers import webhook_deliver as wd

    wh = _mk_webhook(db, events=["share_created"])
    monkeypatch.setattr(wd.httpx, "AsyncClient", lambda *a, **kw: _FakeClient(code=500))

    # First attempt → server error → schedules a retry (status pending).
    res = await wd.webhook_deliver(None, wh.id, "share_created", {})
    assert res["status"] == "retry"
    row = db.query(WebhookDelivery).filter(WebhookDelivery.id == res["delivery_id"]).one()
    assert row.status == WebhookDeliveryStatus.pending

    # Final attempt updates the SAME row → failed (no new row).
    res2 = await wd.webhook_deliver(
        None, wh.id, "share_created", {}, delivery_id=row.id, attempt=5
    )
    assert res2["status"] == "failed"
    db.expire_all()
    assert db.query(WebhookDelivery).filter(WebhookDelivery.webhook_id == wh.id).count() == 1
    row2 = db.query(WebhookDelivery).filter(WebhookDelivery.id == row.id).one()
    assert row2.status == WebhookDeliveryStatus.failed
    assert row2.error == "HTTP 500"


@pytest.mark.asyncio
async def test_webhook_deliver_skips_inactive(db, monkeypatch):
    from app.workers import webhook_deliver as wd

    wh = _mk_webhook(db, events=["share_created"], active=False)
    res = await wd.webhook_deliver(None, wh.id, "share_created", {})
    assert res["skipped"] is True
