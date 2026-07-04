"""v0.3.0 extras on create_share: public_link, group_ids, expires_at_never."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from fileheron_client.api import ApiClient
from fileheron_client.api import shares as shares_api


SERVER = "https://files.example.com"


def _share_response_json(public_link: dict | None = None) -> dict:
    body = {
        "id": "share-1",
        "kind": "outbound",
        "state": "active",
        "subject": "hello",
        "effective_subject": "hello",
        "message": None,
        "created_at": "2026-05-17T10:00:00",
        "expires_at": "2026-05-24T10:00:00",
        "created_by_id": 1,
        "recipient_user_ids": [2],
        "recipient_groups": [],
        "files": [],
    }
    if public_link is not None:
        body["public_link"] = public_link
    return body


@respx.mock
def test_create_share_serializes_expiry_as_utc():
    """A picked expiry is sent as an offset-bearing UTC string, not a tz-less
    string the backend would misread as UTC (shifting expiry by the machine's
    local offset)."""
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_share_response_json())

    respx.post(f"{SERVER}/api/shares").mock(side_effect=_handler)
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    # 18:00 at +02:00 is 16:00 UTC.
    shares_api.create_share(
        api,
        recipient_user_ids=[2],
        expires_at=datetime(2026, 6, 1, 18, 0, 0, tzinfo=timezone(timedelta(hours=2))),
    )
    assert captured["body"]["expires_at"] == "2026-06-01T16:00:00+00:00"
    # A naive local pick still carries a UTC offset (never tz-less).
    assert captured["body"]["expires_at"].endswith("+00:00")


@respx.mock
def test_create_share_with_never_expiry_sends_null():
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        body = _share_response_json()
        body["expires_at"] = None
        return httpx.Response(201, json=body)

    respx.post(f"{SERVER}/api/shares").mock(side_effect=_handler)
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    out = shares_api.create_share(
        api,
        recipient_user_ids=[2],
        expires_at_never=True,
    )
    assert captured["body"]["expires_at"] is None
    assert out.expires_at is None


@respx.mock
def test_create_share_with_public_link_passes_block():
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        body = _share_response_json(
            public_link={
                "id": "pl-1",
                "url": "https://files.example.com/d/abc",
                "download_limit": 5,
                "downloads_remaining": 5,
                "notify_on_download": False,
                "has_password": True,
                "created_at": "2026-05-17T10:00:00",
            }
        )
        return httpx.Response(201, json=body)

    respx.post(f"{SERVER}/api/shares").mock(side_effect=_handler)
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    out = shares_api.create_share(
        api,
        recipient_user_ids=[2],
        public_link={
            "password": "secret",
            "download_limit": 5,
            "notify_on_download": False,
        },
    )
    assert captured["body"]["public_link"]["password"] == "secret"
    assert captured["body"]["public_link"]["download_limit"] == 5
    # extra="ignore" on the client model means we don't parse
    # public_link into a typed field; the wire body + 201 + share id
    # are what we assert.
    assert out.id == "share-1"


@respx.mock
def test_create_share_with_group_ids_passes_them():
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_share_response_json())

    respx.post(f"{SERVER}/api/shares").mock(side_effect=_handler)
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    shares_api.create_share(
        api,
        recipient_user_ids=[2],
        recipient_group_ids=[10, 11],
    )
    assert captured["body"]["recipients"]["group_ids"] == [10, 11]
    assert captured["body"]["recipients"]["user_ids"] == [2]


def test_create_share_rejects_both_expires_args():
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    with pytest.raises(ValueError, match="not both"):
        shares_api.create_share(
            api,
            recipient_user_ids=[2],
            expires_at=datetime(2026, 6, 1),
            expires_at_never=True,
        )
