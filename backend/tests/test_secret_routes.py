"""The secrets API around a reveal (v2.24.0): scopes, who sees which metadata,
the sender's links, burning, /me, and the admin surfaces.

The rules pinned here: only `reveal` returns content; a recipient sees their
own standing and never the roster; the sender and admins see the roster, and
only admins see colleagues' addresses; reading a link back is the sender's
alone, audited, and its own API scope; an admin can burn any secret.
"""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditLog
from app.models.notification import Notification, NotificationCategory
from app.models.secret import Secret, SecretState
from app.models.user import UserRole
from app.services import api_token as api_token_svc
from app.services import secret_reveal

from ._secret_helpers import CONTENT, PW, K, enable, group_with, h, send, setting


@pytest.fixture
def people(make_user):
    a = make_user(email="a@test.local", role=UserRole.employee, password=PW, display_name="Ann")
    b = make_user(email="b@test.local", role=UserRole.employee, password=PW, display_name="Ben")
    admin = make_user(email="adm@test.local", role=UserRole.admin, password=PW, display_name="Adm")
    return a, b, admin


def _api_token(db, owner, scopes: list[str]) -> str:
    _rec, plaintext = api_token_svc.create_token(
        db, owner=owner, name="t", scopes=api_token_svc.normalize_scopes(scopes)
    )
    db.commit()
    return plaintext


# ---- scopes ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reading_metadata_does_not_grant_the_content(db, people, client):
    enable(db)
    a, b, _ = people
    sid = send(db, a, users=(b,)).secret.id
    tok = _api_token(db, b, ["secrets:read"])
    assert (await client.get(f"/api/secrets/{sid}", headers=h(tok))).status_code == 200
    resp = await client.post(f"/api/secrets/{sid}/reveal", json={}, headers=h(tok))
    assert resp.status_code == 403 and resp.json()["code"] == "INSUFFICIENT_SCOPE"
    reveal_tok = _api_token(db, b, ["secrets:reveal"])
    ok = await client.post(f"/api/secrets/{sid}/reveal", json={}, headers=h(reveal_tok))
    assert ok.status_code == 200 and ok.json()["content"] == CONTENT


@pytest.mark.asyncio
async def test_minting_a_link_needs_the_links_scope_too(db, people, client):
    """A new link IS the secret for whoever opens it - a token that may manage a
    secret but not read its links must not be able to mint one."""
    enable(db)
    a, b, _ = people
    sid = send(db, a, users=(b,)).secret.id
    manage_only = _api_token(db, a, ["secrets:manage"])
    resp = await client.post(f"/api/secrets/{sid}/link", headers=h(manage_only))
    assert resp.status_code == 403 and resp.json()["code"] == "INSUFFICIENT_SCOPE"
    both = _api_token(db, a, ["secrets:manage", "secrets:links"])
    ok = await client.post(f"/api/secrets/{sid}/link", headers=h(both))
    assert ok.status_code == 200, ok.text
    assert "/s#" in ok.json()["url"]


# ---- who sees what -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_recipient_sees_their_standing_and_not_the_roster(db, people, make_user, client, login_as):
    enable(db)
    a, b, _ = people
    c = make_user(email="c@test.local", role=UserRole.employee, password=PW)
    sid = send(db, a, users=(b, c), emails=("x@example.com",), max_views=2).secret.id
    token, _ = await login_as(b.email, PW)
    body = (await client.get(f"/api/secrets/{sid}", headers=h(token))).json()
    assert body["viewer_role"] == "recipient"
    assert body["can_reveal"] is True and body["my_views_left"] == 2
    assert body["recipients"] == [] and body["events"] == []
    assert "x@example.com" not in str(body) and "c@test.local" not in str(body)
    assert "content" not in body


@pytest.mark.asyncio
async def test_a_recipient_is_not_told_how_often_the_others_looked(
    db, people, make_user, client, login_as
):
    """`views_used` counts everyone's views: in `per_person` scope it would tell
    one recipient how often the others opened the secret - activity that is the
    sender's to see. A recipient reads `my_views_left` instead."""
    enable(db)
    a, b, admin = people
    c = make_user(email="c@test.local", role=UserRole.employee, password=PW)
    sid = send(db, a, users=(b, c), max_views=2).secret.id
    secret_reveal.reveal_for_user(db, secret_id=sid, user=c, passphrase=None, ip=None)

    tb, _ = await login_as(b.email, PW)
    detail = (await client.get(f"/api/secrets/{sid}", headers=h(tb))).json()
    assert detail["views_used"] is None and detail["my_views_left"] == 2
    received = (await client.get("/api/secrets?box=received", headers=h(tb))).json()
    assert [i["views_used"] for i in received["items"]] == [None]

    ta, _ = await login_as(a.email, PW)
    assert (await client.get(f"/api/secrets/{sid}", headers=h(ta))).json()["views_used"] == 1
    sent = (await client.get("/api/secrets?box=sent", headers=h(ta))).json()
    assert [i["views_used"] for i in sent["items"]] == [1]
    tadm, _ = await login_as(admin.email, PW)
    assert (await client.get(f"/api/secrets/{sid}", headers=h(tadm))).json()["views_used"] == 1


@pytest.mark.asyncio
async def test_the_sender_sees_the_roster_and_link_ips_but_not_colleagues_ips(
    db, people, client, login_as
):
    enable(db)
    a, b, admin = people
    created = send(db, a, users=(b,), link=True, max_views=2)
    sid = created.secret.id
    secret_reveal.reveal_by_token(db, token=created.link_token, passphrase=None, ip="198.51.100.7")
    secret_reveal.reveal_for_user(db, secret_id=sid, user=b, passphrase=None, ip="203.0.113.5")

    token, _ = await login_as(a.email, PW)
    body = (await client.get(f"/api/secrets/{sid}", headers=h(token))).json()
    assert body["viewer_role"] == "sender"
    kinds = sorted(r["kind"] for r in body["recipients"])
    assert kinds == ["link", "user"]
    ips = {e["kind"]: e["ip"] for e in body["events"] if e["outcome"] == "viewed"}
    assert ips == {"link": "198.51.100.7", "user": None}
    assert "content" not in body and CONTENT not in str(body)

    admin_token, _ = await login_as(admin.email, PW)
    admin_body = (await client.get(f"/api/secrets/{sid}", headers=h(admin_token))).json()
    assert admin_body["viewer_role"] == "admin"
    assert {e["ip"] for e in admin_body["events"]} == {"198.51.100.7", "203.0.113.5"}


@pytest.mark.asyncio
async def test_a_group_entry_lists_its_members_for_the_sender(db, people, make_user, client, login_as):
    enable(db)
    a, b, _ = people
    c = make_user(email="c@test.local", role=UserRole.employee, password=PW, display_name="Cat")
    g = group_with(db, a, a, b, c)
    sid = send(db, a, groups=(g,)).secret.id
    secret_reveal.reveal_for_user(db, secret_id=sid, user=b, passphrase=None, ip=None)
    token, _ = await login_as(a.email, PW)
    body = (await client.get(f"/api/secrets/{sid}", headers=h(token))).json()
    (entry,) = body["recipients"]
    assert entry["group"]["name"] == "Ops"
    members = {m["user"]["display_name"]: m["views_used"] for m in entry["members"]}
    assert members == {"Ben": 1, "Cat": 0}


@pytest.mark.asyncio
async def test_the_lists_split_received_and_sent(db, people, client, login_as):
    enable(db)
    a, b, _ = people
    sid = send(db, a, users=(b,), label="Wi-Fi").secret.id
    tb, _ = await login_as(b.email, PW)
    ta, _ = await login_as(a.email, PW)
    received = (await client.get("/api/secrets?box=received", headers=h(tb))).json()
    assert [i["id"] for i in received["items"]] == [sid]
    assert received["items"][0]["my_views_left"] == 1
    assert (await client.get("/api/secrets?box=sent", headers=h(tb))).json()["items"] == []
    sent = (await client.get("/api/secrets?box=sent", headers=h(ta))).json()
    assert sent["items"][0]["recipient_summary"]["users"] == 1
    ended = await client.get("/api/secrets?box=sent&state=burned", headers=h(ta))
    assert ended.json()["items"] == []
    bad = await client.get("/api/secrets?box=sent&state=nonsense", headers=h(ta))
    assert bad.status_code == 422


# ---- links ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_the_sender_reads_links_back_and_each_read_is_audited(
    db, people, client, login_as
):
    enable(db)
    a, b, admin = people
    created = send(db, a, users=(b,), emails=("x@example.com",), link=True)
    sid = created.secret.id
    ta, _ = await login_as(a.email, PW)
    resp = await client.get(f"/api/secrets/{sid}/links", headers=h(ta))
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"].startswith("no-store")
    urls = {i["kind"]: i["url"] for i in resp.json()["items"]}
    assert set(urls) == {"email", "link"}
    assert urls["link"].endswith("#" + created.link_token)
    assert db.query(AuditLog).filter(AuditLog.event_type == "secret_link_shown").count() == 1

    for who in (admin, b):
        tok, _ = await login_as(who.email, PW)
        denied = await client.get(f"/api/secrets/{sid}/links", headers=h(tok))
        assert denied.status_code == 403, f"{who.email} read a link back"


@pytest.mark.asyncio
async def test_removing_the_only_way_in_burns_the_secret(db, people, client, login_as):
    enable(db)
    a, _, _ = people
    sid = send(db, a, link=True).secret.id
    ta, _ = await login_as(a.email, PW)
    resp = await client.delete(f"/api/secrets/{sid}/link", headers=h(ta))
    assert resp.status_code == 204
    db.expire_all()
    assert db.get(Secret, sid).state == SecretState.burned


# ---- burning ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_sender_burns_it_and_it_is_gone(db, people, client, login_as):
    enable(db)
    a, b, _ = people
    sid = send(db, a, users=(b,)).secret.id
    tb, _ = await login_as(b.email, PW)
    denied = await client.post(f"/api/secrets/{sid}/burn", headers=h(tb))
    assert denied.status_code == 403
    ta, _ = await login_as(a.email, PW)
    resp = await client.post(f"/api/secrets/{sid}/burn", headers=h(ta))
    assert resp.status_code == 200 and resp.json()["state"] == "revoked"
    db.expire_all()
    s = db.get(Secret, sid)
    assert s.ciphertext is None and s.key_encrypted is None
    gone = await client.post(f"/api/secrets/{sid}/reveal", json={}, headers=h(tb))
    assert gone.status_code == 410


@pytest.mark.asyncio
async def test_an_admin_burns_anyones_secret_and_the_sender_is_told(db, people, client, login_as):
    enable(db)
    a, b, admin = people
    sid = send(db, a, users=(b,)).secret.id
    tok, _ = await login_as(admin.email, PW)
    resp = await client.post(f"/api/secrets/{sid}/burn", headers=h(tok))
    assert resp.status_code == 200 and resp.json()["state"] == "revoked"
    note = db.query(Notification).filter(
        Notification.user_id == a.id, Notification.category == NotificationCategory.secret_ended
    ).one()
    assert note.payload_json["reason"] == "revoked_by_admin"


# ---- /me and admin ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_reports_the_feature_and_the_ceilings(db, people, make_user, client, login_as):
    a, _, _ = people
    cl = make_user(email="cl@test.local", role=UserRole.client, password=PW)
    ta, _ = await login_as(a.email, PW)
    off = (await client.get("/api/account/me", headers=h(ta))).json()
    assert off["secrets_enabled"] is False and off["secret_limits"] is None
    enable(db)
    on = (await client.get("/api/account/me", headers=h(ta))).json()
    assert on["secrets_enabled"] and on["can_send_secrets"] and on["can_send_secrets_external"]
    assert on["secret_limits"]["max_views"] == 100
    tc, _ = await login_as(cl.email, PW)
    client_me = (await client.get("/api/account/me", headers=h(tc))).json()
    assert client_me["can_send_secrets"] is True
    assert client_me["can_send_secrets_external"] is False


@pytest.mark.asyncio
async def test_the_admin_policy_round_trips_and_is_audited(db, people, client, login_as):
    a, b, admin = people
    tok, _ = await login_as(admin.email, PW)
    resp = await client.put(
        "/api/admin/settings/secrets",
        json={
            "enabled": True,
            "send": {"mode": "employees_admins", "allowed_user_ids": [b.id]},
            "external": {"mode": "admins_only", "allowed_group_ids": []},
            "passphrase_failure_mode": "burn",
        },
        headers=h(tok),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    assert body["send"]["mode"] == "employees_admins"
    assert body["send"]["allowed_users"][0]["display_name"] == "Ben"
    assert body["external"]["mode"] == "admins_only"
    assert body["passphrase_failure_mode"] == "burn"
    row = db.query(AuditLog).filter(AuditLog.event_type == "secret_policy_changed").one()
    assert row.extra["send"]["user_count"] == 1

    bad = await client.put(
        "/api/admin/settings/secrets",
        json={"send": {"mode": "everyone", "allowed_user_ids": [999]}},
        headers=h(tok),
    )
    assert bad.status_code == 400 and bad.json()["code"] == "USER_NOT_FOUND"

    ta, _ = await login_as(a.email, PW)
    assert (await client.get("/api/admin/settings/secrets", headers=h(ta))).status_code == 403


@pytest.mark.asyncio
async def test_the_admin_list_is_metadata_only(db, people, client, login_as):
    enable(db)
    a, b, admin = people
    send(db, a, users=(b,), label="Router admin")
    tok, _ = await login_as(admin.email, PW)
    resp = await client.get("/api/admin/secrets?q=router", headers=h(tok))
    assert resp.status_code == 200, resp.text
    (item,) = resp.json()["items"]
    assert item["label"] == "Router admin" and item["sender_email"] == "a@test.local"
    assert CONTENT not in resp.text


def test_a_partial_policy_put_leaves_the_rest_alone(db, people):
    """Every field is optional; None means unchanged - an SPA one release
    behind must not switch anything off by omission."""
    enable(db, **{K.SECRETS_EXTERNAL_POLICY_MODE: "admins_only"})
    setting(db, K.SECRETS_PASSPHRASE_FAILURE_MODE, "burn")
    from app.routers.admin.settings.secrets import update_secret_policy
    from app.schemas.secret import UpdateSecretPolicyRequest

    _a, _b, admin = people

    class _Req:
        client = None
        state = type("S", (), {})()

    update_secret_policy(UpdateSecretPolicyRequest(enabled=True), _Req(), db, admin)  # type: ignore[arg-type]
    from app.services import secret as secret_svc

    assert secret_svc.resolve_external_policy(db)[0] == "admins_only"
    assert secret_svc.passphrase_failure_mode(db) == "burn"
