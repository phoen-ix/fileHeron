"""With no SMTP host, `utils/emailing.send_email` sends nothing - and what it
does instead depends on the environment (audit M13).

In production, nothing of the mail reaches stdout or the logs but its recipient
and subject. A body carries live one-time tokens (a password reset, an invite, a
secret's link) and container logs are readable by anyone with log access; the
admin mail log keeps a masked copy. Outside production the whole mail is printed
on purpose: `e2e/helpers.ts::tokenFromStdout` reads the reset and register tokens
from exactly that printout, so it has to stay.
"""
from __future__ import annotations

import logging

from app.config import settings
from app.utils.emailing import SmtpConfig, send_email

TOKEN = "Tk3nCanary9f8e7d6c5b4a"
TO = "anna@example.test"
SUBJECT = "Reset your password"


async def _send_without_smtp() -> None:
    cfg = SmtpConfig(
        host="",
        port=587,
        user="",
        password="",
        from_email="noreply@example.test",
        from_name="file:Heron",
    )
    assert not cfg.is_configured
    await send_email(
        cfg=cfg,
        to=TO,
        subject=SUBJECT,
        text_body=f"Reset it here: https://files.example.test/reset-password/{TOKEN}\n",
        html_body=f'<a href="https://files.example.test/reset-password/{TOKEN}">Reset</a>',
    )


async def test_production_prints_and_logs_no_part_of_the_body(monkeypatch, capsys, caplog):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    assert settings.is_production, "the production branch must be the one under test"
    with caplog.at_level(logging.DEBUG):
        await _send_without_smtp()

    out = capsys.readouterr()
    assert TOKEN not in out.out and TOKEN not in out.err
    assert "reset-password" not in out.out
    for record in caplog.records:
        assert TOKEN not in record.getMessage()
        assert TOKEN not in repr(record.__dict__)
    (record,) = [r for r in caplog.records if r.name == "fileheron.email"]
    assert record.levelno == logging.ERROR
    assert "EMAIL NOT SENT" in record.getMessage()
    assert (record.to, record.subject) == (TO, SUBJECT)


async def test_outside_production_the_whole_mail_is_printed(monkeypatch, capsys):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    assert not settings.is_production
    await _send_without_smtp()

    out = capsys.readouterr().out
    # The shape tokenFromStdout matches: /<kind>/<token>.
    assert f"/reset-password/{TOKEN}" in out
    assert f"EMAIL DEV → {TO}" in out
