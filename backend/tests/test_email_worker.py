"""send_email_job - retry semantics + permanent vs transient failure."""
from __future__ import annotations

import pytest
from aiosmtplib.errors import SMTPConnectError, SMTPResponseException
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
