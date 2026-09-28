"""Recipients with no account: the share's public link, sent by email (v2.21.0).

The feature ships OFF and is gated twice - the instance switch
(`share.external_recipients.enabled`) and the public-link policy, because the
address gets nothing a pasted link would not. These tests pin both gates, that
the address is stored and mailed exactly once, that the mail is never sent
before the link works, that the link is masked in the mail log, and that the
addresses are shown only to viewers who may see the whole roster.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.email_log import EmailLog
from app.models.share import Share, ShareKind, ShareState
from app.models.share_external_recipient import ShareExternalRecipient
from app.models.user import UserRole
from app.services import external_recipients as external_svc
from app.services import job_queue, mail_log
from app.services import public_link as public_link_svc
from app.services import settings as settings_svc
from app.services import share as share_svc
from app.services import share_approval as approval_svc

from ._share_helpers import land_file

PW = "Pass12345678!"
K = settings_svc.Keys


def _future_iso(days: int = 7) -> str:
    return (
        datetime.now(tz=timezone.utc).replace(tzinfo=None) + timedelta(days=days)
    ).isoformat()


def _set(db, key: str, value: str) -> None:
    settings_svc.set_value(db, key=key, value=value, actor=None)
    db.commit()


def _enable(db, *, offer_invite: bool = False) -> None:
    _set(db, K.SHARE_EXTERNAL_RECIPIENTS_ENABLED, "true")
    if offer_invite:
        _set(db, K.SHARE_EXTERNAL_RECIPIENTS_OFFER_INVITE, "true")


@pytest.fixture
def sent(monkeypatch):
    """Every job enqueued after a commit. conftest's `enqueue_many` delegates
    to `enqueue`, resolved at call time, so this sees the batched sends."""
    jobs: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        job_queue, "enqueue", lambda name, *_a, **kw: jobs.append((name, kw))
    )
    return jobs


def _body(emails, *, link=True, user_ids=()):
    body = {
        "kind": "outbound",
        "recipients": {"user_ids": list(user_ids), "group_ids": [], "emails": emails},
        "expires_at": _future_iso(),
        "subject": "Q3 figures",
        "message": "See attached.",
    }
    if link:
        body["public_link"] = {
            "password": None,
            "download_limit": None,
            "notify_on_download": False,
        }
    return body


async def _post(client, token, body):
    return await client.post(
        "/api/shares", json=body, headers={"Authorization": f"Bearer {token}"}
    )


# ---- the gates ------------------------------------------------------------


@pytest.mark.asyncio
async def test_refused_while_the_switch_is_off(make_user, db, client, login_as):
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    token, _ = await login_as("emp@test.local", PW)

    resp = await _post(client, token, _body(["ext@example.com"]))

    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "EXTERNAL_RECIPIENTS_DISABLED"
    assert db.query(Share).count() == 0, "refused before anything was written"


@pytest.mark.asyncio
async def test_the_public_link_policy_still_decides(make_user, db, client, login_as):
    """The address rides the public link, so a sender the policy keeps from
    creating links cannot reach anyone this way either."""
    _enable(db)
    _set(db, K.PUBLIC_LINK_POLICY_MODE, "admins_only")
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    token, _ = await login_as("emp@test.local", PW)

    resp = await _post(client, token, _body(["ext@example.com"]))

    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "PUBLIC_LINK_NOT_ALLOWED"
    assert db.query(Share).count() == 0


@pytest.mark.asyncio
async def test_an_address_needs_the_link(make_user, db, client, login_as):
    _enable(db)
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    token, _ = await login_as("emp@test.local", PW)

    resp = await _post(client, token, _body(["ext@example.com"], link=False))

    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "EXTERNAL_RECIPIENT_NEEDS_LINK"
    assert db.query(Share).count() == 0


@pytest.mark.asyncio
async def test_a_client_cannot_address_anyone(make_user, db, client, login_as):
    """Clients create inbound shares; their recipients are ignored, but a mail
    to an outside address must not be silently dropped either."""
    _enable(db)
    _set(db, K.PUBLIC_LINK_POLICY_MODE, "everyone")
    make_user(email="cl@test.local", role=UserRole.client, password=PW)
    token, _ = await login_as("cl@test.local", PW)

    resp = await _post(client, token, _body(["ext@example.com"]))

    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "FORBIDDEN_KIND"


@pytest.mark.asyncio
async def test_a_malformed_address_is_a_422(make_user, db, client, login_as):
    _enable(db)
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    token, _ = await login_as("emp@test.local", PW)

    resp = await _post(client, token, _body(["not-an-address"]))

    assert resp.status_code == 422, resp.text


# ---- create + announce ----------------------------------------------------


@pytest.mark.asyncio
async def test_addresses_are_stored_normalised_and_once(make_user, db, client, login_as):
    _enable(db)
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    token, _ = await login_as("emp@test.local", PW)

    resp = await _post(
        client, token, _body(["Ext@Example.com", "ext@example.com ", "two@example.com"])
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["external_recipients"] == ["ext@example.com", "two@example.com"]
    rows = db.query(ShareExternalRecipient).filter_by(share_id=body["id"]).all()
    assert sorted(r.email for r in rows) == ["ext@example.com", "two@example.com"]
    assert all(r.notified_at is None for r in rows), "nothing is mailed at create time"


def _external_share(db, make_user, *, emails=("ext@example.com",), password=None):
    """An active outbound share with an inline link and external rows, built
    the way the create route builds it."""
    _enable(db)
    sender = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        external_emails=list(emails),
        expires_at=datetime.now(tz=timezone.utc) + timedelta(days=1),
        subject="Q3 figures",
        allow_no_recipients=True,
    )
    created = public_link_svc.create_link(
        db,
        actor=sender,
        share=share,
        password=password,
        download_limit=None,
        notify_on_download=False,
    )
    db.commit()
    return sender, share, created.plaintext_token


def _link_mails(jobs):
    """Sends to the outside addresses only - staff in these tests are
    `@test.local`, and an approval-held share mails its approvers."""
    return [
        kw for name, kw in jobs
        if name == "send_email_job" and kw["to"].endswith("@example.com")
    ]


def test_the_link_goes_out_once_the_files_land(db, make_user, sent):
    sender, share, token = _external_share(
        db, make_user, emails=("ext@example.com", "two@example.com")
    )
    assert _link_mails(sent) == [], "an empty share announces nothing"

    land_file(db, share, sender, name="q3.xlsx")
    assert share_svc.announce_if_ready(db, share.id)
    db.commit()

    mails = _link_mails(sent)
    assert sorted(m["to"] for m in mails) == ["ext@example.com", "two@example.com"]
    url = public_link_svc.public_url(db, token)
    for m in mails:
        assert url in m["text_body"]
        assert f'href="{url}"' in m["html_body"], "canonical href form, or masking misses it"
        assert m.get("list_unsubscribe") is None, "no account, no preferences to manage"
    rows = db.query(ShareExternalRecipient).filter_by(share_id=share.id).all()
    assert all(r.notified_at is not None for r in rows)

    # A second announcement on the same share mails nobody twice.
    sent.clear()
    share.notify_on_activation = True
    db.commit()
    share_svc.announce_if_ready(db, share.id)
    db.commit()
    assert _link_mails(sent) == []


def test_the_mail_log_never_holds_the_live_link(db, make_user, sent):
    sender, share, token = _external_share(db, make_user)
    land_file(db, share, sender)
    share_svc.announce_if_ready(db, share.id)
    db.commit()

    row = db.query(EmailLog).filter_by(recipient_email="ext@example.com").one()
    assert row.category == external_svc.MAIL_CATEGORY
    assert row.recipient_user_id is None
    assert row.masked is True
    assert token not in (row.body_text or "")
    assert token not in (row.body_html or "")
    assert "<redacted>" in (row.body_text or "")


def test_a_password_is_mentioned_and_never_sent(db, make_user, sent):
    sender, share, _token = _external_share(db, make_user, password="Sekrit-pass-42")
    land_file(db, share, sender)
    share_svc.announce_if_ready(db, share.id)
    db.commit()

    (mail,) = _link_mails(sent)
    assert "password" in mail["text_body"].lower()
    assert "Sekrit-pass-42" not in mail["text_body"]
    assert "Sekrit-pass-42" not in (mail["html_body"] or "")


def test_a_quiet_share_mails_nobody(db, make_user, sent):
    """The sender's "notify recipients" choice covers these addresses too."""
    _enable(db)
    sender = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        external_emails=["ext@example.com"],
        expires_at=None,
        allow_no_recipients=True,
        notify_recipients=False,
    )
    public_link_svc.create_link(
        db, actor=sender, share=share, password=None, download_limit=None,
        notify_on_download=False,
    )
    db.commit()
    land_file(db, share, sender)
    share_svc.announce_if_ready(db, share.id)
    db.commit()

    assert _link_mails(sent) == []


def test_a_revoked_link_is_not_mailed(db, make_user, sent):
    sender, share, _token = _external_share(db, make_user)
    link = public_link_svc.get_active_link_for_share(db, share.id)
    public_link_svc.revoke(db, actor=sender, link=link)
    db.commit()
    land_file(db, share, sender)
    share_svc.announce_if_ready(db, share.id)
    db.commit()

    assert _link_mails(sent) == []
    row = db.query(ShareExternalRecipient).filter_by(share_id=share.id).one()
    assert row.notified_at is None, "an address that was never told must read as such"


# ---- approval -------------------------------------------------------------


def _enable_approval(db, *, scope: str) -> None:
    _set(db, K.SHARE_APPROVAL_ENABLED, "true")
    _set(db, K.SHARE_APPROVAL_APPROVER_MODE, "admins_only")
    _set(db, K.SHARE_APPROVAL_SCOPE, scope)
    _set(db, K.SHARE_APPROVAL_EXEMPT_APPROVERS, "true")


def test_an_address_counts_as_leaving_the_organisation(db, make_user, sent):
    """`outbound_to_clients` asks "does this leave the org". An address with no
    account is outside it by definition, so the share is held - and the link
    mail waits for the decision."""
    _enable_approval(db, scope="outbound_to_clients")
    admin = make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    sender, share, _token = _external_share(db, make_user)
    assert share.state == ShareState.pending_approval

    land_file(db, share, sender)
    share_svc.announce_if_ready(db, share.id)
    db.commit()
    assert _link_mails(sent) == [], "no mail while the share awaits approval"

    share_svc.approve_share(
        db, user=admin, share=share,
        expect_fingerprint=approval_svc.content_fingerprint(db, share),
    )
    db.commit()
    assert [m["to"] for m in _link_mails(sent)] == ["ext@example.com"]


def test_an_internal_only_share_is_still_not_held(db, make_user):
    """Control for the test above: the new check must not hold a share that
    reaches only staff."""
    _enable_approval(db, scope="outbound_to_clients")
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    sender = make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    colleague = make_user(email="col@test.local", role=UserRole.employee, password=PW)
    share = share_svc.create_share(
        db,
        created_by=sender,
        kind=ShareKind.outbound,
        recipient_user_ids=[colleague.id],
        expires_at=None,
    )
    db.commit()
    assert share.state == ShareState.active


# ---- who sees the addresses ----------------------------------------------


@pytest.mark.asyncio
async def test_only_the_full_roster_sees_the_addresses(make_user, db, client, login_as):
    _enable(db)
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    colleague = make_user(email="col@test.local", role=UserRole.employee, password=PW)
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    token, _ = await login_as("emp@test.local", PW)
    resp = await _post(
        client, token, _body(["ext@example.com"], user_ids=[colleague.id])
    )
    assert resp.status_code == 201, resp.text
    share_id = resp.json()["id"]

    async def _seen_by(email):
        tok, _ = await login_as(email, PW)
        r = await client.get(
            f"/api/shares/{share_id}", headers={"Authorization": f"Bearer {tok}"}
        )
        assert r.status_code == 200, r.text
        return r.json()["external_recipients"]

    assert await _seen_by("emp@test.local") == ["ext@example.com"]
    assert await _seen_by("admin@test.local") == ["ext@example.com"]
    assert await _seen_by("col@test.local") == [], "a co-recipient is not told"


# ---- /me + settings -------------------------------------------------------


@pytest.mark.asyncio
async def test_me_flags_follow_the_switches_and_the_policy(make_user, db, client, login_as):
    make_user(email="emp@test.local", role=UserRole.employee, password=PW)
    make_user(email="cl@test.local", role=UserRole.client, password=PW)

    async def _me(email):
        tok, _ = await login_as(email, PW)
        r = await client.get("/api/account/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200, r.text
        return r.json()["can_share_external"], r.json()["offer_invite_on_external"]

    assert await _me("emp@test.local") == (False, False), "off by default"
    _enable(db)
    assert await _me("emp@test.local") == (True, False)
    _set(db, K.SHARE_EXTERNAL_RECIPIENTS_OFFER_INVITE, "true")
    assert await _me("emp@test.local") == (True, True)
    _set(db, K.PUBLIC_LINK_POLICY_MODE, "everyone")
    assert await _me("cl@test.local") == (False, False), "never for a client"
    _set(db, K.PUBLIC_LINK_POLICY_MODE, "admins_only")
    assert await _me("emp@test.local") == (False, False), "the link policy gates it"


@pytest.mark.asyncio
async def test_settings_round_trip_and_an_old_spa_changes_nothing(
    make_user, db, client, login_as
):
    make_user(email="admin@test.local", role=UserRole.admin, password=PW)
    token, _ = await login_as("admin@test.local", PW)
    hdr = {"Authorization": f"Bearer {token}"}
    url = "/api/admin/settings/public-links/policy"

    got = (await client.get(url, headers=hdr)).json()
    assert got["external_recipients_enabled"] is False
    assert got["external_recipients_offer_invite"] is False

    put = await client.put(
        url,
        json={
            "mode": "employees_admins",
            "allowed_user_ids": [],
            "allowed_group_ids": [],
            "external_recipients_enabled": True,
            "external_recipients_offer_invite": True,
        },
        headers=hdr,
    )
    assert put.status_code == 200, put.text
    assert put.json()["external_recipients_enabled"] is True
    assert put.json()["external_recipients_offer_invite"] is True

    # An SPA one release behind sends only the three old fields.
    old = await client.put(
        url,
        json={"mode": "employees_admins", "allowed_user_ids": [], "allowed_group_ids": []},
        headers=hdr,
    )
    assert old.status_code == 200, old.text
    assert old.json()["external_recipients_enabled"] is True
    assert old.json()["external_recipients_offer_invite"] is True


def test_the_masking_pattern_follows_the_base_path():
    """The public-link path in the mask comes from the same setting the link
    builder reads; a literal `/d/` would leak live tokens on an instance that
    moved it."""
    from app.config import settings

    base = settings.PUBLIC_LINK_BASE_PATH
    masked, did = mail_log.mask_sensitive(f"https://x.test{base}/LIVETOKEN123 ok")
    assert did and "LIVETOKEN123" not in (masked or "")
    # Negative control: an ordinary share URL is left alone.
    plain, did2 = mail_log.mask_sensitive("https://x.test/share/abc-123")
    assert not did2 and plain == "https://x.test/share/abc-123"
