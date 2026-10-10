"""send_email_job - retry semantics + permanent vs transient failure."""
from __future__ import annotations

import pytest
from aiosmtplib.errors import (
    SMTPConnectError,
    SMTPRecipientRefused,
    SMTPRecipientsRefused,
    SMTPResponseException,
)
from arq import Retry

from app.workers import send_email as worker_mod


@pytest.mark.asyncio
async def test_send_email_job_returns_sent_on_happy_path(monkeypatch):
    calls = []

    async def _fake(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(worker_mod, "send_email", _fake)
    ctx = {"job_try": 1}
    result = await worker_mod.send_email_job(
        ctx,
        to="to@example.com",
        subject="Hi",
        text_body="hello",
        html_body=None,
    )
    assert result == {"to": "to@example.com", "subject": "Hi", "status": "sent"}
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_send_email_job_retries_on_transient(monkeypatch):
    async def _fake(**kwargs):
        raise SMTPConnectError("connection refused")

    monkeypatch.setattr(worker_mod, "send_email", _fake)

    with pytest.raises(Retry):
        await worker_mod.send_email_job(
            {"job_try": 1},
            to="x@example.com",
            subject="s",
            text_body="t",
        )
    # We don't introspect Retry.defer (the attribute name varies across
    # ARQ versions); raising the Retry is the contract that matters -
    # ARQ then schedules the next attempt.


@pytest.mark.asyncio
async def test_send_email_job_gives_up_on_permanent_5xx(monkeypatch):
    async def _fake(**kwargs):
        raise SMTPResponseException(550, "no such mailbox")

    monkeypatch.setattr(worker_mod, "send_email", _fake)
    result = await worker_mod.send_email_job(
        {"job_try": 1},
        to="bad@example.com",
        subject="s",
        text_body="t",
    )
    assert result["status"] == "failed"
    assert result["code"] == 550


@pytest.mark.asyncio
async def test_send_email_job_retries_on_transient_4xx(monkeypatch):
    async def _fake(**kwargs):
        raise SMTPResponseException(421, "service shutting down")

    monkeypatch.setattr(worker_mod, "send_email", _fake)
    with pytest.raises(Retry):
        await worker_mod.send_email_job(
            {"job_try": 2},
            to="x@example.com",
            subject="s",
            text_body="t",
        )


# --- the bounce shape aiosmtplib ACTUALLY raises -----------------------------
#
# The 5xx test above raises SMTPResponseException, which is real for a DATA-time
# rejection, a refused sender or an auth failure - but NOT for a refused
# recipient. utils/emailing.py always sets a single To, so one bad address means
# all recipients refused, and aiosmtplib raises SMTPRecipientsRefused (plural),
# which subclasses SMTPException and carries `.recipients` rather than `.code`.
# It therefore missed the 5xx branch entirely and landed in the generic handler:
# status `error`, smtp_code NULL, no audit row - and ops_check's smtp_failing
# alert plus email_undeliverable_24h count only those audit rows.


def _refusal(code: int = 550, message: str = "no such mailbox"):
    return SMTPRecipientsRefused([SMTPRecipientRefused(code, message, "bad@example.com")])


@pytest.mark.asyncio
async def test_a_refused_recipient_is_a_permanent_failure(monkeypatch):
    async def _fake(**kwargs):
        raise _refusal()

    audited: list[tuple] = []
    monkeypatch.setattr(worker_mod, "send_email", _fake)
    monkeypatch.setattr(
        worker_mod, "_record_undeliverable_audit",
        lambda to, subject, code, msg: audited.append((to, code, msg)),
    )

    result = await worker_mod.send_email_job(
        {"job_try": 1}, to="bad@example.com", subject="s", text_body="t",
    )

    assert result["status"] == "failed"
    assert result["code"] == 550
    # The audit row is the whole point: it is the only thing ops_check reads.
    assert audited == [("bad@example.com", 550, "no such mailbox")]


@pytest.mark.asyncio
async def test_a_refusal_with_no_detail_still_audits(monkeypatch):
    """`.recipients` can be empty. An IndexError here would drop the row back
    into the generic handler and reintroduce the silence."""
    async def _fake(**kwargs):
        raise SMTPRecipientsRefused([])

    audited: list[tuple] = []
    monkeypatch.setattr(worker_mod, "send_email", _fake)
    monkeypatch.setattr(
        worker_mod, "_record_undeliverable_audit",
        lambda to, subject, code, msg: audited.append((to, code, msg)),
    )

    result = await worker_mod.send_email_job(
        {"job_try": 1}, to="bad@example.com", subject="s", text_body="t",
    )

    assert result["status"] == "failed"
    assert len(audited) == 1


# --- How long a transient failure is retried ---------------------------------
#
# On 2026-10-07 20:14 UTC the reference host's network dropped for 3-5 minutes.
# The schedule was 1 s, 5 s, 30 s, 30 s - giving up 2 min 47 s after the first
# try - so all three mails sent in that window failed for good while SMTP was
# answering again by 20:19.


def test_the_retry_schedule_outlasts_an_ordinary_outage():
    waits = worker_mod._RETRY_DEFERS_SEC
    assert sum(waits) >= 3600
    assert list(waits) == sorted(waits)  # backs off, never speeds up
    assert worker_mod._defer_for(1) == waits[0]
    assert worker_mod._defer_for(worker_mod._MAX_TRIES + 3) == waits[-1]


def test_the_worker_registers_the_try_budget_the_job_finalizes_on():
    """arq abandons a job after its last try WITHOUT calling it again, so the
    job must know which try is the last - from the same number arq uses."""
    from app.workers import worker

    entry = next(
        f
        for f in worker.WorkerSettings.functions
        if (getattr(f, "name", None) or getattr(f, "__name__", "")) == "send_email_job"
    )
    assert entry.max_tries == worker_mod._MAX_TRIES == len(worker_mod._RETRY_DEFERS_SEC) + 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [SMTPConnectError("connection refused"), SMTPResponseException(421, "busy")]
)
async def test_each_failed_try_waits_its_turn_until_the_last(error, monkeypatch):
    async def _fake(**kwargs):
        raise error

    monkeypatch.setattr(worker_mod, "send_email", _fake)
    for attempt in range(1, worker_mod._MAX_TRIES):
        with pytest.raises(Retry) as exc:
            await worker_mod.send_email_job(
                {"job_try": attempt}, to="x@example.com", subject="s", text_body="t"
            )
        # arq is pinned; defer_score is its millisecond form of `defer`.
        assert exc.value.defer_score == worker_mod._defer_for(attempt) * 1000
    last = await worker_mod.send_email_job(
        {"job_try": worker_mod._MAX_TRIES}, to="x@example.com", subject="s", text_body="t"
    )
    assert last["status"] == "failed"
