"""The secret-request API (v2.24.0): scopes, what each role sees, the
requester's links, the anonymous answer page and the admin surfaces.
"""
from __future__ import annotations

import pytest

from app.models.audit_log import AuditLog
from app.models.user import UserRole
from app.services import api_token as api_token_svc

from ._secret_helpers import PW, ask, enable, h

ANSWER = "router admin: Hx9#qL2v!"


@pytest.fixture
def people(make_user):
    ann = make_user(email="ann@test.local", role=UserRole.employee, password=PW, display_name="Ann")
    ben = make_user(email="ben@test.local", role=UserRole.employee, password=PW, display_name="Ben")
    admin = make_user(email="adm@test.local", role=UserRole.admin, password=PW, display_name="Adm")
    return ann, ben, admin


def _api_token(db, owner, scopes: list[str]) -> str:
    _rec, plaintext = api_token_svc.create_token(
        db, owner=owner, name="t", scopes=api_token_svc.normalize_scopes(scopes)
    )
    db.commit()
    return plaintext


def _body(**over) -> dict:
    from datetime import timedelta

    from app.utils.timeutil import utc_now

    body = {
        "label": "Router password",
        "note": "The admin one, please.",
        "expires_at": (utc_now() + timedelta(days=7)).isoformat(),
        "answer_max_views": 1,
        "recipients": {"user_ids": [], "group_ids": [], "emails": []},
    }
    body.update(over)
    return body


@pytest.mark.asyncio
async def test_asking_needs_the_request_scope_and_answering_the_send_scope(db, people, client):
    enable(db)
    ann, ben, _ = people
    read_only = _api_token(db, ann, ["secrets:read"])
    body = _body(recipients={"user_ids": [ben.id], "group_ids": [], "emails": []})
    resp = await client.post("/api/secret-requests", json=body, headers=h(read_only))
    assert resp.status_code == 403 and resp.json()["code"] == "INSUFFICIENT_SCOPE"
    asker = _api_token(db, ann, ["secrets:request"])
    created = await client.post("/api/secret-requests", json=body, headers=h(asker))
    assert created.status_code == 201, created.text
    rid = created.json()["id"]

    request_only = _api_token(db, ben, ["secrets:request"])
    resp = await client.post(
        f"/api/secret-requests/{rid}/answer", json={"content": ANSWER}, headers=h(request_only)
    )
    assert resp.status_code == 403 and resp.json()["code"] == "INSUFFICIENT_SCOPE"
    sender = _api_token(db, ben, ["secrets:send"])
    ok = await client.post(
        f"/api/secret-requests/{rid}/answer", json={"content": ANSWER}, headers=h(sender)
    )
    assert ok.status_code == 200 and ok.json() == {"ok": True, "requester_name": "Ann"}
    assert ANSWER not in ok.text


@pytest.mark.asyncio
async def test_each_role_sees_what_it_may(db, people, make_user, client, login_as):
    enable(db)
    ann, ben, admin = people
    stranger = make_user(email="x@test.local", role=UserRole.employee, password=PW)
    req = ask(db, ann, users=(ben,), emails=("guest@example.com",), note="Thanks!").request

    tb, _ = await login_as(ben.email, PW)
    as_target = (await client.get(f"/api/secret-requests/{req.id}", headers=h(tb))).json()
    assert as_target["viewer_role"] == "target" and as_target["can_answer"] is True
    assert as_target["note"] == "Thanks!" and as_target["targets"] == []
    assert "guest@example.com" not in str(as_target)

    ta, _ = await login_as(ann.email, PW)
    as_requester = (await client.get(f"/api/secret-requests/{req.id}", headers=h(ta))).json()
    assert as_requester["viewer_role"] == "requester"
    assert sorted(t["kind"] for t in as_requester["targets"]) == ["email", "user"]

    tadm, _ = await login_as(admin.email, PW)
    as_admin = (await client.get(f"/api/secret-requests/{req.id}", headers=h(tadm))).json()
    assert as_admin["viewer_role"] == "admin"

    ts, _ = await login_as(stranger.email, PW)
    resp = await client.get(f"/api/secret-requests/{req.id}", headers=h(ts))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_only_the_requester_reads_links_back_and_each_read_is_audited(
    db, people, client, login_as
):
    enable(db)
    ann, _, admin = people
    created = ask(db, ann, link=True)
    rid = created.request.id
    ta, _ = await login_as(ann.email, PW)
    resp = await client.get(f"/api/secret-requests/{rid}/links", headers=h(ta))
    assert resp.status_code == 200
    assert resp.headers["cache-control"].startswith("no-store")
    (item,) = resp.json()["items"]
    assert item["url"].endswith("/r#" + created.link_token)
    assert (
        db.query(AuditLog).filter(AuditLog.event_type == "secret_request_link_shown").count() == 1
    )
    tadm, _ = await login_as(admin.email, PW)
    resp = await client.get(f"/api/secret-requests/{rid}/links", headers=h(tadm))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_the_answer_page_peeks_for_free_and_answers_once(db, people, client):
    enable(db)
    ann, *_ = people
    created = ask(db, ann, link=True, note="The admin one.", passphrase="requester's own pw")
    token = created.link_token

    peek = await client.post("/api/public/secret-requests/peek", json={"token": token})
    assert peek.status_code == 200 and peek.headers["cache-control"].startswith("no-store")
    body = peek.json()
    assert body["requester_name"] == "Ann" and body["label"] == "Router password"
    assert body["note"] == "The admin one." and body["has_passphrase"] is True
    # Peeking twice changes nothing.
    assert (await client.post("/api/public/secret-requests/peek", json={"token": token})).status_code == 200

    ok = await client.post(
        "/api/public/secret-requests/answer", json={"token": token, "content": ANSWER}
    )
    assert ok.status_code == 200 and ok.json()["requester_name"] == "Ann"
    again = await client.post(
        "/api/public/secret-requests/answer", json={"token": token, "content": "second"}
    )
    assert again.status_code == 410 and again.json()["code"] == "SECRET_REQUEST_CLOSED"
    closed = await client.post("/api/public/secret-requests/peek", json={"token": token})
    assert closed.status_code == 410 and closed.json()["details"] == {"reason": "fulfilled"}
    unknown = await client.post(
        "/api/public/secret-requests/peek", json={"token": "x" * 43}
    )
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_the_requester_opens_the_answer_with_their_passphrase(db, people, client, login_as):
    enable(db)
    ann, *_ = people
    created = ask(db, ann, link=True, passphrase="requester's own pw")
    await client.post(
        "/api/public/secret-requests/answer",
        json={"token": created.link_token, "content": ANSWER},
    )
    ta, _ = await login_as(ann.email, PW)
    box = (await client.get("/api/secrets?box=received", headers=h(ta))).json()
    (item,) = box["items"]
    assert item["is_answer"] is True and item["answered_via"] == "link"
    assert item["sender"] is None and item["label"] == "Router password"
    detail = (await client.get(f"/api/secrets/{item['id']}", headers=h(ta))).json()
    assert detail["has_request_passphrase"] is True and detail["can_burn"] is True
    wrong = await client.post(
        f"/api/secrets/{item['id']}/reveal", json={"request_passphrase": "nope nope"}, headers=h(ta)
    )
    assert wrong.status_code == 403 and wrong.json()["code"] == "SECRET_REQUEST_PASSPHRASE_INVALID"
    right = await client.post(
        f"/api/secrets/{item['id']}/reveal",
        json={"request_passphrase": "requester's own pw"},
        headers=h(ta),
    )
    assert right.status_code == 200 and right.json()["content"] == ANSWER

    mine = (await client.get(f"/api/secret-requests/{created.request.id}", headers=h(ta))).json()
    assert mine["answered_via"] == "link" and mine["answer_secret_id"] == item["id"]


@pytest.mark.asyncio
async def test_the_list_boxes_split_mine_and_asked(db, people, client, login_as):
    enable(db)
    ann, ben, _ = people
    req = ask(db, ann, users=(ben,)).request
    ta, _ = await login_as(ann.email, PW)
    tb, _ = await login_as(ben.email, PW)
    mine = (await client.get("/api/secret-requests?box=mine", headers=h(ta))).json()
    assert [i["id"] for i in mine["items"]] == [req.id]
    assert mine["items"][0]["target_summary"]["users"] == 1
    assert (await client.get("/api/secret-requests?box=asked", headers=h(ta))).json()["items"] == []
    asked = (await client.get("/api/secret-requests?box=asked", headers=h(tb))).json()
    assert [i["id"] for i in asked["items"]] == [req.id] and asked["items"][0]["can_answer"] is True


@pytest.mark.asyncio
async def test_the_admin_lists_and_cancels_requests(db, people, client, login_as):
    enable(db)
    ann, ben, admin = people
    req = ask(db, ann, users=(ben,)).request
    tadm, _ = await login_as(admin.email, PW)
    listed = (await client.get("/api/admin/secret-requests", headers=h(tadm))).json()
    (item,) = listed["items"]
    assert item["id"] == req.id and item["requester_email"] == "ann@test.local"
    resp = await client.post(f"/api/admin/secret-requests/{req.id}/cancel", headers=h(tadm))
    assert resp.status_code == 200 and resp.json()["state"] == "cancelled"
    ta, _ = await login_as(ann.email, PW)
    assert (await client.get("/api/admin/secret-requests", headers=h(ta))).status_code == 403
