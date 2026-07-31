"""A fan-out must not cost one Redis connection per recipient.

`dos-15` (audit 2026-07-30): `notification.dispatch` registered a separate
after-commit thunk per recipient, and the sync `job_queue.enqueue` runs
`asyncio.run(aenqueue(...))` - which builds an event loop, calls
`arq.create_pool`, pushes one job and closes the pool again. A share to twenty
people therefore built twenty event loops and twenty connection pools, serially,
on the request thread, while the sender waited for the HTTP response. The cost
scaled with a number the *user* chooses.

The jobs now accumulate on the session and one flush thunk pushes the batch over
a single pool. What is deliberately NOT batched: the render, the notification
row and the mail-log row stay inline per recipient. Moving those behind the
queue would change how fast the in-app bell lights up, and would mean a Redis
outage produced no rows at all rather than rows whose sends can be retried.

These tests count pools, because that is the thing that was wrong.
"""
from __future__ import annotations

import asyncio

import pytest

from app.models.notification import NotificationCategory
from app.models.user import UserRole
from app.services import job_queue as _jq

# Captured at import, before conftest's autouse stub replaces them.
_REAL = {
    name: getattr(_jq, name)
    for name in ("enqueue", "aenqueue", "enqueue_many", "aenqueue_many")
}


class _FakePool:
    """Counts what a real ARQ pool would have cost."""

    created = 0
    jobs: list[tuple] = []

    def __init__(self):
        type(self).created += 1
        self.closed = False

    async def enqueue_job(self, name, *args, **kwargs):
        kwargs.pop("_queue_name", None)
        type(self).jobs.append((name, args, kwargs))
        return object()

    async def aclose(self):
        self.closed = True


@pytest.fixture
def pool_counter(monkeypatch):
    """conftest's autouse `_no_op_job_queue` stubs the enqueue entry points out
    entirely, which is right for tests that only care about their side effects
    and useless here - the pool count IS the subject. Put the real functions
    back and intercept one layer lower, at `create_pool`."""
    from app.services import job_queue

    _FakePool.created = 0
    _FakePool.jobs = []

    async def _create_pool(*a, **k):
        return _FakePool()

    for name, fn in _REAL.items():
        monkeypatch.setattr(job_queue, name, fn)
    monkeypatch.setattr(job_queue, "create_pool", _create_pool)
    return _FakePool


# --- the primitive ----------------------------------------------------------


def test_a_batch_opens_one_pool(pool_counter):
    from app.services import job_queue

    job_queue.enqueue_many(
        [("send_email_job", (), {"to": f"u{i}@test.local"}) for i in range(20)]
    )
    assert pool_counter.created == 1
    assert len(pool_counter.jobs) == 20


def test_an_empty_batch_opens_none(pool_counter):
    from app.services import job_queue

    job_queue.enqueue_many([])
    assert pool_counter.created == 0


def test_single_enqueue_still_opens_one_per_call(pool_counter):
    """The contrast the fix is about: this is the old fan-out's per-recipient
    cost, and it is still correct behaviour for a genuinely single job."""
    from app.services import job_queue

    for i in range(5):
        job_queue.enqueue("av_scan_file", f"file-{i}")
    assert pool_counter.created == 5


def test_the_batch_carries_the_same_arguments(pool_counter):
    from app.services import job_queue

    job_queue.enqueue_many(
        [
            ("send_email_job", (), {"to": "a@test.local", "subject": "one"}),
            ("send_email_job", (), {"to": "b@test.local", "subject": "two"}),
        ]
    )
    assert pool_counter.jobs == [
        ("send_email_job", (), {"to": "a@test.local", "subject": "one"}),
        ("send_email_job", (), {"to": "b@test.local", "subject": "two"}),
    ]


def test_a_redis_failure_is_logged_not_raised(monkeypatch, caplog, pool_counter):
    """A missed notification email must never fail the action that produced it."""
    from app.services import job_queue

    async def _boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(job_queue, "create_pool", _boom)
    job_queue.enqueue_many([("send_email_job", (), {"to": "a@test.local"})])
    assert "failed to enqueue batch" in caplog.text


def test_a_failure_does_not_log_the_message_body(monkeypatch, caplog, pool_counter):
    """Enqueue args carry the rendered email and a live unsubscribe token; the
    single-job path learned this the hard way and the batch path must not
    reintroduce it."""
    from app.services import job_queue

    async def _boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(job_queue, "create_pool", _boom)
    job_queue.enqueue_many(
        [
            (
                "send_email_job",
                (),
                {
                    "to": "victim@test.local",
                    "text_body": "SECRET-BODY",
                    "list_unsubscribe": "<https://x/u/LIVE-TOKEN>",
                    "email_log_id": 7,
                },
            )
        ]
    )
    assert "SECRET-BODY" not in caplog.text
    assert "LIVE-TOKEN" not in caplog.text
    assert "victim@test.local" not in caplog.text
    assert "email_log_id=7" in caplog.text  # enough to find which job failed


@pytest.mark.asyncio
async def test_a_batch_from_inside_a_running_loop_still_enqueues(pool_counter):
    """`asyncio.run` inside a live loop raises and would drop the batch."""
    from app.services import job_queue

    job_queue.enqueue_many([("send_email_job", (), {"to": "a@test.local"})])
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert pool_counter.created == 1
    assert len(pool_counter.jobs) == 1


# --- the notification fan-out -----------------------------------------------


def _dispatch_to(db, users):
    from app.services import notification as notif_svc

    for u in users:
        notif_svc.dispatch(
            db,
            user=u,
            category=NotificationCategory.share_created,
            payload={"sender_name": "Someone", "subject": "A share", "share_id": "s-1"},
            email_to=u.email,
        )


def test_ten_recipients_cost_one_pool(db, make_user, pool_counter):
    users = [
        make_user(email=f"r{i}@test.local", role=UserRole.employee) for i in range(10)
    ]
    _dispatch_to(db, users)
    assert pool_counter.created == 0, "nothing may be pushed before the commit"
    db.commit()
    assert pool_counter.created == 1
    assert len(pool_counter.jobs) == 10


def test_every_recipient_still_gets_a_job(db, make_user, pool_counter):
    users = [
        make_user(email=f"s{i}@test.local", role=UserRole.employee) for i in range(4)
    ]
    _dispatch_to(db, users)
    db.commit()
    sent_to = sorted(kw["to"] for _n, _a, kw in pool_counter.jobs)
    assert sent_to == sorted(u.email for u in users)
    assert all(name == "send_email_job" for name, _a, _kw in pool_counter.jobs)


def test_every_job_still_names_its_mail_log_row(db, make_user, pool_counter):
    """The worker finalizes the row it was given; a batch that lost the id
    would leave every queued row stuck in `queued` forever."""
    from app.models.email_log import EmailLog

    users = [
        make_user(email=f"t{i}@test.local", role=UserRole.employee) for i in range(3)
    ]
    _dispatch_to(db, users)
    db.commit()
    ids = {kw["email_log_id"] for _n, _a, kw in pool_counter.jobs}
    assert len(ids) == 3
    assert ids <= {row.id for row in db.query(EmailLog).all()}


def test_a_rollback_still_drops_the_whole_batch(db, make_user, pool_counter):
    """The mail-log rows are written in the caller's transaction, so a rollback
    must take the emails with it (audit M8) - a job whose row never persisted
    makes the worker fail on a row it cannot find."""
    users = [
        make_user(email=f"u{i}@test.local", role=UserRole.employee) for i in range(3)
    ]
    db.commit()
    pool_counter.created = 0
    pool_counter.jobs = []

    _dispatch_to(db, users)
    db.rollback()
    db.commit()
    assert pool_counter.created == 0
    assert pool_counter.jobs == []


def test_a_rolled_back_batch_is_not_adopted_by_the_next_dispatch(
    db, make_user, pool_counter
):
    """The subtle half: the flush thunk is dropped on rollback, but the pending
    list lives on the session. Left behind, the NEXT dispatch would find a
    non-empty batch, register no thunk of its own, and either send the
    rolled-back emails or never send its own."""
    doomed = [
        make_user(email=f"v{i}@test.local", role=UserRole.employee) for i in range(3)
    ]
    survivor = make_user(email="survivor@test.local", role=UserRole.employee)
    db.commit()
    pool_counter.created = 0
    pool_counter.jobs = []

    _dispatch_to(db, doomed)
    db.rollback()

    _dispatch_to(db, [survivor])
    db.commit()
    assert [kw["to"] for _n, _a, kw in pool_counter.jobs] == ["survivor@test.local"]


def test_a_second_commit_starts_a_fresh_batch(db, make_user, pool_counter):
    """The flush pops rather than reads: a session reused after its commit must
    not re-send what already went out."""
    a = make_user(email="a1@test.local", role=UserRole.employee)
    b = make_user(email="b1@test.local", role=UserRole.employee)
    db.commit()
    pool_counter.created = 0
    pool_counter.jobs = []

    _dispatch_to(db, [a])
    db.commit()
    _dispatch_to(db, [b])
    db.commit()
    assert pool_counter.created == 2
    assert [kw["to"] for _n, _a, kw in pool_counter.jobs] == [
        "a1@test.local",
        "b1@test.local",
    ]


def test_an_in_app_only_recipient_adds_nothing_to_the_batch(
    db, make_user, pool_counter
):
    from app.models.user_notification_preference import (
        NotificationChannel,
        UserNotificationPreference,
    )

    quiet = make_user(email="quiet@test.local", role=UserRole.employee)
    loud = make_user(email="loud@test.local", role=UserRole.employee)
    db.add(
        UserNotificationPreference(
            user_id=quiet.id,
            category=NotificationCategory.share_created,
            channel=NotificationChannel.in_app,
        )
    )
    db.commit()
    pool_counter.created = 0
    pool_counter.jobs = []

    _dispatch_to(db, [quiet, loud])
    db.commit()
    assert [kw["to"] for _n, _a, kw in pool_counter.jobs] == ["loud@test.local"]


# --- the webhook fan-out ----------------------------------------------------


def test_webhook_delivery_also_batches(db, pool_counter):
    from app.models.webhook import Webhook
    from app.services import webhook as webhook_svc
    from app.utils.crypto import encrypt_setting

    for i in range(6):
        db.add(
            Webhook(
                name=f"hook-{i}",
                url=f"https://example.invalid/{i}",
                secret_encrypted=encrypt_setting("s"),
                event_types=["*"],
                active=True,
            )
        )
    db.commit()
    pool_counter.created = 0
    pool_counter.jobs = []

    n = webhook_svc.emit(db, "share_created", {"share_id": "s-1"})
    assert n == 6
    assert pool_counter.created == 1
    assert len(pool_counter.jobs) == 6


def test_an_unsubscribed_webhook_is_not_in_the_batch(db, pool_counter):
    from app.models.webhook import Webhook
    from app.services import webhook as webhook_svc
    from app.utils.crypto import encrypt_setting

    db.add(
        Webhook(
            name="only-uploads",
            url="https://example.invalid/a",
            secret_encrypted=encrypt_setting("s"),
            event_types=["file_uploaded"],
            active=True,
        )
    )
    db.add(
        Webhook(
            name="all",
            url="https://example.invalid/b",
            secret_encrypted=encrypt_setting("s"),
            event_types=["*"],
            active=True,
        )
    )
    db.commit()
    pool_counter.created = 0
    pool_counter.jobs = []

    assert webhook_svc.emit(db, "share_created", {"share_id": "s-1"}) == 1
    assert len(pool_counter.jobs) == 1


def test_a_webhook_emit_failure_never_reaches_the_caller(db, monkeypatch, caplog):
    from app.services import webhook as webhook_svc

    def _boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(webhook_svc.job_queue, "enqueue_many", _boom)
    assert webhook_svc.emit(db, "share_created", {"share_id": "s-1"}) == 0
    assert "webhook.emit failed" in caplog.text
