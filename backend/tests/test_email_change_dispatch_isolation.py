"""One bounced email must not cost a user their only way back in.

flow-emailchange-2: `dispatch_request_emails` wrapped the old-address alert, the
set-password link and the completion notice in a single try/except, and sent the
old-address alert FIRST. Everything in that block runs after the change is
committed and the OIDC binding has been reset - so a 550 on a decommissioned old
mailbox (the admin-changes-the-address-of-a-departed-employee case, i.e. exactly
what the feature is for) aborted the block before the set-password link went
out. The user was left unlinked from SSO, with no password, no reset link and no
notice that anything had happened. Silent, because the handler logs and returns.

Fixed by sending each message independently and putting whatever restores access
first (audit 2026-07-30).
"""
from __future__ import annotations

import pytest

from app.services import email_change as ec


class _Recorder:
    """Stands in for services.email; blows up on the addresses it is told to."""

    def __init__(self, *, fail_to=()):
        self.sent: list[tuple[str, str]] = []
        self._fail_to = set(fail_to)

    def _record(self, kind):
        async def _send(*, to, **_kw):
            if to in self._fail_to:
                raise RuntimeError(f"SMTP 550 {to}")
            self.sent.append((kind, to))

        return _send

    def __getattr__(self, name):
        return self._record(name)


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder(fail_to={"old@test.local"})

    import app.services.email as email_svc

    for name in (
        "send_email_change_alert",
        "send_password_reset_email",
        "send_email_change_completed",
        "send_email_change_confirm",
        "send_email_change_verify_old",
    ):
        monkeypatch.setattr(email_svc, name, getattr(rec, name), raising=False)
    return rec


def _applied_outcome(**kw):
    base = {
        "applied": True,
        "user_id": 1,
        "old_email": "old@test.local",
        "new_email": "new@test.local",
        "locale": "en",
        "display_name": "Dana",
        "by_admin": True,
        "oidc_reset": True,
        "set_password_token": "tok-123",
        "mode": "immediate",
        "new_token": None,
        "old_token": None,
        "cancel_token": None,
    }
    base.update(kw)
    return ec.RequestOutcome(**base)


@pytest.mark.asyncio
async def test_a_bounce_on_the_old_address_still_sends_the_set_password_link(
    db, recorder
):
    await ec.dispatch_request_emails(db, _applied_outcome())

    kinds = {k for k, _to in recorder.sent}
    assert "send_password_reset_email" in kinds, (
        "the old mailbox bounced and took the user's only credential path with it"
    )
    assert "send_email_change_completed" in kinds


@pytest.mark.asyncio
async def test_access_restoring_mail_goes_out_first(db, recorder):
    """Ordering is part of the fix: if the set-password link is queued behind
    two courtesy mails, a transient SMTP failure mid-batch still loses it."""
    await ec.dispatch_request_emails(db, _applied_outcome())
    assert recorder.sent[0][0] == "send_password_reset_email"


@pytest.mark.asyncio
async def test_nothing_is_dropped_when_every_address_works(db, monkeypatch):
    """Control: the isolation must not change what gets sent in the happy
    path."""
    rec = _Recorder()
    import app.services.email as email_svc

    for name in (
        "send_email_change_alert", "send_password_reset_email",
        "send_email_change_completed",
    ):
        monkeypatch.setattr(email_svc, name, getattr(rec, name), raising=False)

    await ec.dispatch_request_emails(db, _applied_outcome())

    assert {k for k, _ in rec.sent} == {
        "send_password_reset_email",
        "send_email_change_completed",
        "send_email_change_alert",
    }


@pytest.mark.asyncio
async def test_pending_mode_confirm_survives_a_bounced_old_address(db, recorder):
    """The pending flow has the same shape: the confirm link is what moves it
    forward, and it must not depend on the old mailbox accepting mail."""
    outcome = _applied_outcome(
        applied=False, mode="verify_new", new_token="ntok",
        cancel_token="ctok", set_password_token=None,
    )
    await ec.dispatch_request_emails(db, outcome)
    assert ("send_email_change_confirm", "new@test.local") in recorder.sent


@pytest.mark.asyncio
async def test_confirm_dispatch_is_also_isolated(db, monkeypatch):
    """The confirm-time dispatcher had the same single-try shape; a failing
    set-password send must not swallow the completion notice."""
    rec = _Recorder()
    import app.services.email as email_svc

    async def _boom(**_kw):
        raise RuntimeError("SMTP 421")

    monkeypatch.setattr(email_svc, "send_password_reset_email", _boom, raising=False)
    monkeypatch.setattr(
        email_svc, "send_email_change_completed",
        rec.send_email_change_completed, raising=False,
    )

    await ec.dispatch_confirm_emails(
        db,
        ec.ConfirmOutcome(
            applied=True, new_email="new@test.local", locale="en",
            display_name="Dana", oidc_reset=True, set_password_token="tok",
        ),
    )
    assert ("send_email_change_completed", "new@test.local") in rec.sent
