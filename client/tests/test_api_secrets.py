"""Secrets + secret requests on the wire (server v2.24.0).

What each call sends - method, path, query, body - and that the client's
mirrors accept what the server really answers (an answer written without an
account has no sender; an older server's /me has no secret fields at all).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from fileheron_client.api import ApiClient, ApiError
from fileheron_client.api import secret_requests as req_api
from fileheron_client.api import secrets as sec_api
from fileheron_client.models import MeResponse

SERVER = "https://files.example.com"
NOW = "2026-10-02T09:00:00"


def _api() -> ApiClient:
    return ApiClient(SERVER, api_token="fh_xx_yy")


def _secret(**over) -> dict:
    body = {
        "id": "s1", "state": "active", "label": "VPN", "sender": {"id": 2, "display_name": "Alex"},
        "created_at": NOW, "ended_at": None, "expires_at": "2026-10-09T09:00:00", "max_views": 1,
        "view_scope": "per_person", "views_used": None, "has_passphrase": True,
        "has_request_passphrase": False, "notify_on_view": False, "burn_after_failures": None,
        "viewer_role": "recipient", "is_answer": False, "request_id": None, "answered_via": None,
        "answered_by_email": None, "can_burn": False, "my_views_left": 1, "can_reveal": True,
        "still_recipient": True, "burned_for_me": False, "my_failed_attempts": 0,
        "recipients": [], "recipient_summary": {"users": 0, "groups": 0, "emails": 0, "link": False},
        "events": [], "link_url": None, "link_qr_svg": None,
    }
    body.update(over)
    return body


def _request(**over) -> dict:
    body = {
        "id": "r1", "state": "open", "closed_reason": None, "label": "Router", "note": None,
        "requester": {"id": 1, "display_name": "Me"}, "created_at": NOW,
        "expires_at": "2026-10-09T09:00:00", "ended_at": None, "answer_max_views": 1,
        "answer_expires_in_sec": 604800, "has_passphrase": False, "viewer_role": "requester",
        "can_answer": False, "targets": [],
        "target_summary": {"users": 1, "groups": 0, "emails": 0, "link": False},
        "fulfilled_at": None, "answered_via": None, "answered_by": None,
        "answered_by_email": None, "answer_secret_id": None, "link_url": None, "link_qr_svg": None,
    }
    body.update(over)
    return body


def _capture(route_method, path: str, response: httpx.Response) -> dict:
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        captured["body"] = json.loads(request.content) if request.content else None
        return response

    route_method(f"{SERVER}{path}").mock(side_effect=_handler)
    return captured


# ---- secrets ---------------------------------------------------------------------


@respx.mock
def test_list_secrets_sends_the_box_and_each_state_as_its_own_param():
    cap = _capture(respx.get, "/api/secrets", httpx.Response(200, json={
        "items": [_secret(sender=None, is_answer=True, answered_via="link")],
        "total": 1, "page": 1, "page_size": 200,
    }))
    out = sec_api.list_secrets(_api(), box="sent", q="vpn", states=["burned", "expired", "revoked"])
    params = cap["url"].params
    assert params["box"] == "sent"
    assert params["q"] == "vpn"
    assert params.get_list("state") == ["burned", "expired", "revoked"]
    assert params["page_size"] == "200"
    # An answer written without an account has no sender.
    assert out.items[0].sender is None and out.items[0].answered_via == "link"


@respx.mock
def test_list_secrets_without_a_filter_sends_no_state():
    cap = _capture(respx.get, "/api/secrets", httpx.Response(200, json={"items": []}))
    sec_api.list_secrets(_api(), box="received", states=[])
    assert "state" not in cap["url"].params
    assert "q" not in cap["url"].params


@respx.mock
def test_create_secret_sends_the_whole_form_and_expects_201():
    cap = _capture(respx.post, "/api/secrets", httpx.Response(201, json=_secret(
        viewer_role="sender", link_url="https://files.example.com/s#tok")))
    at = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)
    out = sec_api.create_secret(
        _api(), content="hunter2", label="VPN", passphrase="correct horse", max_views=3,
        view_scope="per_recipient", expires_at=at, user_ids=[2], group_ids=[7],
        emails=["guest@example.com"], create_link=True, notify_on_view=True, burn_on_failures=True,
    )
    body = cap["body"]
    assert body["content"] == "hunter2"
    assert body["passphrase"] == "correct horse"
    assert body["max_views"] == 3 and body["view_scope"] == "per_recipient"
    assert body["expires_at"] == "2026-10-09T09:00:00+00:00"
    assert body["recipients"] == {"user_ids": [2], "group_ids": [7], "emails": ["guest@example.com"]}
    assert body["create_link"] is True and body["notify_on_view"] is True
    assert body["burn_on_failures"] is True
    assert out.link_url == "https://files.example.com/s#tok"


@respx.mock
def test_create_secret_without_expiry_or_passphrase():
    cap = _capture(respx.post, "/api/secrets", httpx.Response(201, json=_secret()))
    sec_api.create_secret(_api(), content="x", max_views=1, burn_on_failures=True)
    body = cap["body"]
    assert body["expires_at"] is None
    assert body["passphrase"] is None and body["label"] is None
    # Burning on wrong passphrases means nothing without a passphrase.
    assert body["burn_on_failures"] is False


@respx.mock
def test_create_secret_turns_an_offset_into_utc():
    cap = _capture(respx.post, "/api/secrets", httpx.Response(201, json=_secret()))
    at = datetime(2026, 10, 9, 11, 0, tzinfo=timezone(timedelta(hours=2)))
    sec_api.create_secret(_api(), content="x", expires_at=at)
    assert cap["body"]["expires_at"] == "2026-10-09T09:00:00+00:00"


@respx.mock
def test_reveal_sends_both_passphrases_in_the_body_never_the_url():
    cap = _capture(respx.post, "/api/secrets/s1/reveal", httpx.Response(200, json={
        "content": "hunter2", "views_left": 0, "ended": True,
    }))
    out = sec_api.reveal_secret(_api(), "s1", passphrase="sender-pass",
                                request_passphrase="my-request-pass")
    assert cap["body"] == {"passphrase": "sender-pass", "request_passphrase": "my-request-pass"}
    assert "pass" not in str(cap["url"])
    assert out.content == "hunter2" and out.ended is True and out.views_left == 0


@respx.mock
def test_reveal_without_passphrases_sends_nulls():
    cap = _capture(respx.post, "/api/secrets/s1/reveal", httpx.Response(200, json={
        "content": "x", "views_left": None, "ended": False,
    }))
    sec_api.reveal_secret(_api(), "s1", passphrase="", request_passphrase=None)
    assert cap["body"] == {"passphrase": None, "request_passphrase": None}


@respx.mock
@pytest.mark.parametrize("code", ["SECRET_PASSPHRASE_INVALID", "SECRET_REQUEST_PASSPHRASE_INVALID"])
def test_a_wrong_passphrase_is_an_ordinary_error_not_a_dead_session(code):
    respx.post(f"{SERVER}/api/secrets/s1/reveal").mock(return_value=httpx.Response(403, json={
        "error": "no", "code": code, "details": None, "request_id": "r",
    }))
    with pytest.raises(ApiError) as exc:
        sec_api.reveal_secret(_api(), "s1", passphrase="wrong")
    assert exc.value.code == code
    assert type(exc.value) is ApiError  # not SessionExpiredError


@respx.mock
def test_get_secret_parses_the_sender_view():
    respx.get(f"{SERVER}/api/secrets/s9").mock(return_value=httpx.Response(200, json=_secret(
        id="s9", viewer_role="sender", views_used=1, can_burn=True,
        recipients=[{
            "id": 1, "kind": "group", "user": None, "group": {"id": 7, "name": "Ops"}, "email": None,
            "views_used": 0, "views_left": 2, "failed_attempts": 0, "locked_until": None,
            "burned": False, "revoked": False, "emailed_at": None, "created_at": NOW,
            "members": [{"user": {"id": 4, "display_name": "Cy"}, "views_used": 0,
                         "last_viewed_at": None, "eligible": False, "burned": False}],
        }],
        events=[{"at": NOW, "outcome": "viewed", "recipient_id": 1, "kind": "email",
                 "user": None, "email": "guest@example.com", "ip": "203.0.113.9"}],
    )))
    out = sec_api.get_secret(_api(), "s9")
    assert out.recipients[0].group.name == "Ops"
    assert out.recipients[0].members[0].eligible is False
    assert out.events[0].ip == "203.0.113.9"


@respx.mock
def test_burn_and_links():
    burned = respx.post(f"{SERVER}/api/secrets/s1/burn").mock(
        return_value=httpx.Response(200, json=_secret(state="revoked")))
    links = respx.get(f"{SERVER}/api/secrets/s1/links").mock(return_value=httpx.Response(200, json={
        "items": [{"recipient_id": 3, "kind": "email", "email": "a@example.com",
                   "url": "https://files.example.com/s#t", "qr_svg": "<svg/>"},
                  {"recipient_id": 4, "kind": "link", "email": None, "url": None, "qr_svg": None}],
    }))
    assert sec_api.burn_secret(_api(), "s1").state == "revoked"
    out = sec_api.get_secret_links(_api(), "s1")
    assert burned.called and links.called
    assert out.items[0].url.endswith("#t") and out.items[1].url is None


# ---- requests --------------------------------------------------------------------


@respx.mock
def test_list_requests_sends_the_box_and_states():
    cap = _capture(respx.get, "/api/secret-requests", httpx.Response(200, json={
        "items": [{**_request(), "target_summary": None}], "total": 1,
    }))
    out = req_api.list_secret_requests(_api(), box="asked", states=["open"])
    assert cap["url"].params["box"] == "asked"
    assert cap["url"].params.get_list("state") == ["open"]
    assert out.items[0].label == "Router"


@respx.mock
def test_create_request_sends_the_limits_and_the_requester_passphrase():
    cap = _capture(respx.post, "/api/secret-requests", httpx.Response(201, json=_request(
        link_url="https://files.example.com/r#tok")))
    at = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)
    out = req_api.create_secret_request(
        _api(), label="Router", note="2nd floor", expires_at=at, answer_max_views=1,
        answer_expires_in_sec=604800, passphrase="my request pass", user_ids=[3],
        emails=["it@example.com"], create_link=True,
    )
    body = cap["body"]
    assert body["label"] == "Router" and body["note"] == "2nd floor"
    assert body["expires_at"] == "2026-10-09T09:00:00+00:00"
    assert body["answer_max_views"] == 1 and body["answer_expires_in_sec"] == 604800
    assert body["passphrase"] == "my request pass"
    assert body["recipients"] == {"user_ids": [3], "group_ids": [], "emails": ["it@example.com"]}
    assert body["create_link"] is True
    assert out.link_url.endswith("/r#tok")


@respx.mock
def test_answer_sends_the_text_and_the_answerers_passphrase_in_the_body():
    cap = _capture(respx.post, "/api/secret-requests/r2/answer", httpx.Response(200, json={
        "ok": True, "requester_name": "Alex",
    }))
    out = req_api.answer_secret_request(_api(), "r2", content="s3cret", passphrase="")
    assert cap["body"] == {"content": "s3cret", "passphrase": None}
    assert out.ok is True and out.requester_name == "Alex"


@respx.mock
def test_get_cancel_and_links():
    respx.get(f"{SERVER}/api/secret-requests/r1").mock(return_value=httpx.Response(200, json=_request(
        state="fulfilled", closed_reason="fulfilled", answer_secret_id="s2", answered_via="link",
        targets=[{"id": 1, "kind": "link", "user": None, "group": None, "email": None,
                  "notified_at": None}],
    )))
    respx.post(f"{SERVER}/api/secret-requests/r1/cancel").mock(
        return_value=httpx.Response(200, json=_request(state="cancelled", closed_reason="cancelled")))
    respx.get(f"{SERVER}/api/secret-requests/r1/links").mock(return_value=httpx.Response(200, json={
        "items": [{"target_id": 1, "kind": "link", "email": None,
                   "url": "https://files.example.com/r#t", "qr_svg": None}],
    }))
    got = req_api.get_secret_request(_api(), "r1")
    assert got.answer_secret_id == "s2" and got.targets[0].kind == "link"
    assert req_api.cancel_secret_request(_api(), "r1").state == "cancelled"
    assert req_api.get_secret_request_links(_api(), "r1").items[0].url.endswith("#t")


# ---- /me ---------------------------------------------------------------------------


def test_an_older_server_hides_secrets():
    me = MeResponse.model_validate({
        "id": 1, "email": "a@example.com", "display_name": "A", "role": "employee", "locale": "en",
    })
    assert me.secrets_enabled is False and me.can_send_secrets is False
    assert me.secret_limits is None


def test_me_carries_the_secret_limits():
    me = MeResponse.model_validate({
        "id": 1, "email": "a@example.com", "display_name": "A", "role": "employee", "locale": "en",
        "secrets_enabled": True, "can_send_secrets": True, "can_send_secrets_external": False,
        "secret_limits": {"max_views": 5, "max_expiry_days": 30, "max_lifetime_days": 0,
                          "passphrase_failure_mode": "burn", "passphrase_max_failures": 3},
    })
    assert me.secrets_enabled and me.can_send_secrets and not me.can_send_secrets_external
    assert me.secret_limits.max_views == 5
    assert me.secret_limits.passphrase_failure_mode == "burn"
