"""Sending a secret (v2.24.0): who may, to whom, with which limits.

The feature ships OFF and has two policy gates of its own - sending at all
(default everyone) and sending out of the organisation, to an address or as a
link (default staff; a client never). A client can only reach the employees
they are connected to. At least one limit is required, the admin ceilings hold,
and the lifetime ceiling ends a views-only secret too.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.audit_log import AuditLog
from app.models.email_log import EmailLog
from app.models.notification import Notification, NotificationCategory
from app.models.secret import (
    Secret,
    SecretGroupMember,
    SecretRecipient,
    SecretRecipientKind,
    SecretUserState,
    SecretViewScope,
)
from app.models.user import UserRole
from app.services import job_queue
from app.utils.timeutil import utc_now

from ._secret_helpers import (
    CONTENT,
    PW,
    K,
    connect,
    enable,
    group_with,
    h,
    send,
    setting,
    token_of,
)


def _body(**over):
    body = {
        "content": CONTENT,
        "label": "VPN",
        "max_views": 1,
        "view_scope": "per_person",
        "expires_at": (utc_now() + timedelta(days=7)).isoformat(),
        "recipients": {"user_ids": [], "group_ids": [], "emails": []},
        "create_link": False,
    }
    body.update(over)
    return body


@pytest.fixture
def sent(monkeypatch):
    jobs: list[tuple[str, dict]] = []
    monkeypatch.setattr(job_queue, "enqueue", lambda name, *_a, **kw: jobs.append((name, kw)))
    return jobs


@pytest.fixture
def staff(make_user):
    emp = make_user(email="emp@test.local", role=UserRole.employee, password=PW, display_name="Emp")
    col = make_user(email="col@test.local", role=UserRole.employee, password=PW, display_name="Col")
    return emp, col


async def _post(client, login_as, email, body):
    token, _ = await login_as(email, PW)
    return await client.post("/api/secrets", json=body, headers=h(token))


# ---- the gates ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_refused_while_the_switch_is_off(db, staff, client, login_as):
    emp, col = staff
    resp = await _post(client, login_as, emp.email, _body(recipients={"user_ids": [col.id]}))
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "SECRETS_DISABLED"
    assert db.query(Secret).count() == 0


@pytest.mark.asyncio
async def test_the_send_policy_decides(db, staff, client, login_as):
    enable(db, **{K.SECRETS_SEND_POLICY_MODE: "admins_only"})
    emp, col = staff
    resp = await _post(client, login_as, emp.email, _body(recipients={"user_ids": [col.id]}))
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "SECRET_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_a_client_may_send_to_a_connected_employee_by_default(
    db, staff, make_user, client, login_as
):
    enable(db)
    emp, _ = staff
    cl = make_user(email="cl@test.local", role=UserRole.client, password=PW)
    connect(db, cl, emp)
    resp = await _post(client, login_as, cl.email, _body(recipients={"user_ids": [emp.id]}))
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_a_client_cannot_reach_an_unconnected_employee(db, staff, make_user, client, login_as):
    enable(db)
    emp, _ = staff
    cl = make_user(email="cl@test.local", role=UserRole.client, password=PW)
    resp = await _post(client, login_as, cl.email, _body(recipients={"user_ids": [emp.id]}))
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "RECIPIENT_NOT_CONNECTED"


@pytest.mark.asyncio
async def test_a_client_cannot_address_a_group(db, staff, make_user, client, login_as):
    enable(db)
    emp, col = staff
    cl = make_user(email="cl@test.local", role=UserRole.client, password=PW)
    g = group_with(db, emp, emp, col, inbox=True)
    resp = await _post(client, login_as, cl.email, _body(recipients={"group_ids": [g.id]}))
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "SECRET_RECIPIENT_NOT_ALLOWED"


@pytest.mark.asyncio
@pytest.mark.parametrize("external", [{"recipients": {"emails": ["x@example.com"]}}, {"create_link": True}])
async def test_a_client_never_sends_out_of_the_organisation(
    db, make_user, client, login_as, external
):
    """Not even when the external policy says everyone: an address or a link
    leaves the organisation, and a client is not who sends it."""
    enable(db, **{K.SECRETS_EXTERNAL_POLICY_MODE: "everyone"})
    cl = make_user(email="cl@test.local", role=UserRole.client, password=PW)
    resp = await _post(client, login_as, cl.email, _body(**external))
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "SECRET_EXTERNAL_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_the_external_policy_is_its_own_gate(db, staff, client, login_as):
    enable(db, **{K.SECRETS_EXTERNAL_POLICY_MODE: "admins_only"})
    emp, col = staff
    ok = await _post(client, login_as, emp.email, _body(recipients={"user_ids": [col.id]}))
    assert ok.status_code == 201, ok.text
    resp = await _post(client, login_as, emp.email, _body(create_link=True))
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "SECRET_EXTERNAL_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_staff_follow_the_share_addressing_rules(db, staff, make_user, client, login_as):
    """An employee reaches a client only through a connection - the same rule a
    share follows (share._validate_outbound_targets)."""
    enable(db)
    emp, _ = staff
    cl = make_user(email="cl@test.local", role=UserRole.client, password=PW)
    resp = await _post(client, login_as, emp.email, _body(recipients={"user_ids": [cl.id]}))
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "RECIPIENT_NOT_CONNECTED"


@pytest.mark.asyncio
async def test_sending_to_yourself_is_refused(db, staff, client, login_as):
    enable(db)
    emp, _ = staff
    resp = await _post(client, login_as, emp.email, _body(recipients={"user_ids": [emp.id]}))
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "SELF_SHARE"


# ---- limits ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_at_least_one_limit_is_required(db, staff, client, login_as):
    enable(db)
    emp, col = staff
    resp = await _post(
        client, login_as, emp.email,
        _body(max_views=None, expires_at=None, recipients={"user_ids": [col.id]}),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "SECRET_NEEDS_LIMIT"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "over",
    [{"max_views": 101}, {"expires_at": (utc_now() + timedelta(days=91)).isoformat()}],
)
async def test_the_admin_ceilings_hold(db, staff, client, login_as, over):
    enable(db)
    emp, col = staff
    resp = await _post(client, login_as, emp.email, _body(recipients={"user_ids": [col.id]}, **over))
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "SECRET_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_an_expiry_in_the_past_is_refused(db, staff, client, login_as):
    enable(db)
    emp, col = staff
    resp = await _post(
        client, login_as, emp.email,
        _body(expires_at=(utc_now() - timedelta(minutes=1)).isoformat(), recipients={"user_ids": [col.id]}),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "EXPIRY_IN_PAST"


def test_the_lifetime_ceiling_ends_a_views_only_secret(db, staff):
    enable(db)
    emp, col = staff
    created = send(db, emp, users=(col,), max_views=3, expires_in=None)
    assert created.secret.expires_at is not None
    delta = created.secret.expires_at - utc_now()
    assert timedelta(days=89, hours=23) < delta <= timedelta(days=90)


def test_with_no_lifetime_ceiling_a_views_only_secret_has_no_expiry(db, staff):
    enable(db, **{K.SECRETS_MAX_LIFETIME_DAYS: "0"})
    emp, col = staff
    created = send(db, emp, users=(col,), max_views=3, expires_in=None)
    assert created.secret.expires_at is None


def test_the_lifetime_ceiling_also_caps_a_chosen_expiry(db, staff):
    enable(db, **{K.SECRETS_MAX_LIFETIME_DAYS: "2"})
    emp, col = staff
    created = send(db, emp, users=(col,), expires_in=timedelta(days=30))
    assert created.secret.expires_at <= utc_now() + timedelta(days=2)


def test_view_scope_is_ignored_without_a_view_limit(db, staff):
    enable(db)
    emp, col = staff
    created = send(db, emp, users=(col,), max_views=None, scope=SecretViewScope.total)
    assert created.secret.view_scope == SecretViewScope.per_person


@pytest.mark.asyncio
async def test_no_recipient_and_no_link_is_refused(db, staff, client, login_as):
    enable(db)
    emp, _ = staff
    resp = await _post(client, login_as, emp.email, _body())
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "SECRET_NO_RECIPIENTS"


# ---- what gets written ----------------------------------------------------------


def test_group_members_are_snapshotted_without_the_sender_or_disabled(db, staff, make_user):
    enable(db)
    emp, col = staff
    off = make_user(email="off@test.local", role=UserRole.employee, is_disabled=True)
    g = group_with(db, emp, emp, col, off)
    created = send(db, emp, groups=(g,))
    rec = db.query(SecretRecipient).filter_by(secret_id=created.secret.id).one()
    members = {m.user_id for m in db.query(SecretGroupMember).filter_by(recipient_id=rec.id)}
    assert members == {col.id}
    states = {s.user_id for s in db.query(SecretUserState).filter_by(secret_id=created.secret.id)}
    assert states == {col.id}


def test_burn_mode_is_frozen_onto_the_secret(db, staff):
    enable(db)
    emp, col = staff
    assert send(db, emp, users=(col,), passphrase="long enough", burn=True).secret.burn_after_failures == 10
    assert send(db, emp, users=(col,), passphrase="long enough").secret.burn_after_failures is None
    # Without a passphrase there is nothing to burn on.
    assert send(db, emp, users=(col,), burn=True).secret.burn_after_failures is None
    setting(db, K.SECRETS_PASSPHRASE_FAILURE_MODE, "burn")
    assert send(db, emp, users=(col,), passphrase="long enough").secret.burn_after_failures == 10


@pytest.mark.asyncio
async def test_the_response_carries_no_content_and_the_link_in_the_fragment(
    db, staff, client, login_as
):
    enable(db)
    emp, col = staff
    resp = await _post(
        client, login_as, emp.email,
        _body(recipients={"user_ids": [col.id]}, create_link=True),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert CONTENT not in resp.text
    assert "content" not in body
    url = body["link_url"]
    assert "/s#" in url, url
    tok = token_of(url)
    assert tok not in url.split("#")[0]
    assert body["link_qr_svg"].startswith("<")
    assert resp.headers.get("cache-control", "").startswith("no-store")
    assert body["viewer_role"] == "sender"
    assert body["recipient_summary"] == {"users": 1, "groups": 0, "emails": 0, "link": True}


def test_account_recipients_are_told_without_the_content(db, staff, make_user):
    enable(db)
    emp, col = staff
    third = make_user(email="third@test.local", role=UserRole.employee)
    # The sender may only address a group they belong to (the share rule), and
    # is never told about their own secret.
    g = group_with(db, emp, emp, third)
    created = send(db, emp, users=(col,), groups=(g,))
    notes = db.query(Notification).filter(
        Notification.category == NotificationCategory.secret_received
    ).all()
    assert {n.user_id for n in notes} == {col.id, third.id}
    for n in notes:
        assert CONTENT not in str(n.payload_json)
        assert n.link_url.endswith(f"/secrets/{created.secret.id}")


def test_each_address_gets_its_own_link_by_mail(db, staff, sent):
    enable(db)
    emp, _ = staff
    send(db, emp, emails=("a@example.com", "b@example.com"), passphrase="long enough")
    mails = [kw for name, kw in sent if name == "send_email_job"]
    assert sorted(m["to"] for m in mails) == ["a@example.com", "b@example.com"]
    links = {token_of(next(w for w in m["text_body"].split() if "/s#" in w)) for m in mails}
    assert len(links) == 2, "both addresses got the same link"
    for m in mails:
        assert CONTENT not in m["text_body"] and CONTENT not in (m["html_body"] or "")
        assert "long enough" not in m["text_body"], "the passphrase is never mailed"
    rows = db.query(EmailLog).filter(EmailLog.category == "secret_link_external").all()
    assert len(rows) == 2
    for row in rows:
        assert row.masked is True
        for tok in links:
            assert tok not in (row.body_text or "") and tok not in (row.body_html or "")
        assert "/s#<redacted>" in (row.body_text or "")
    recs = db.query(SecretRecipient).filter_by(kind=SecretRecipientKind.email).all()
    assert all(r.notified_at is not None for r in recs)


def test_the_audit_row_counts_addresses_and_never_names_them(db, staff):
    enable(db)
    emp, _ = staff
    created = send(db, emp, emails=("a@example.com",), link=True)
    row = db.query(AuditLog).filter(AuditLog.event_type == "secret_created").one()
    assert row.target_id == created.secret.id
    assert row.extra["email_count"] == 1
    assert "a@example.com" not in str(row.extra)
    assert CONTENT not in str(row.extra)
