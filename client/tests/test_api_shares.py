"""Share manager actions (v0.2.0): revoke / expire-now / patch-expiry."""
from __future__ import annotations

from datetime import datetime

import httpx
import pytest
import respx

from fileheron_client.api import ApiClient
from fileheron_client.api import shares as shares_api


SERVER = "https://files.example.com"


def _share_response_json(share_id: str = "share-1", state: str = "active") -> dict:
    return {
        "id": share_id,
        "kind": "outbound",
        "state": state,
        "subject": "test",
        "effective_subject": "test",
        "message": None,
        "created_at": "2026-05-17T10:00:00",
        "expires_at": "2026-05-24T10:00:00",
        "created_by_id": 1,
        "recipient_user_ids": [2],
        "recipient_groups": [],
        "files": [],
    }


@respx.mock
def test_revoke_share_calls_delete_endpoint():
    route = respx.delete(f"{SERVER}/api/shares/share-1").mock(
        return_value=httpx.Response(204)
    )
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    shares_api.revoke_share(api, "share-1")
    assert route.called


@respx.mock
def test_expire_share_now_returns_updated_share():
    body = _share_response_json(state="expired")
    body["expires_at"] = "2026-05-17T10:05:00"
    route = respx.post(f"{SERVER}/api/shares/share-1/expire").mock(
        return_value=httpx.Response(200, json=body)
    )
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    out = shares_api.expire_share_now(api, "share-1")
    assert route.called
    assert out.state == "expired"
    assert out.expires_at is not None


@respx.mock
def test_patch_share_expiry_set_sends_expires_at():
    body = _share_response_json()
    body["expires_at"] = "2026-06-01T18:00:00"
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=body)

    respx.patch(f"{SERVER}/api/shares/share-1").mock(side_effect=_handler)
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    out = shares_api.patch_share_expiry(
        api, "share-1", expires_at=datetime(2026, 6, 1, 18, 0, 0)
    )
    assert b'"expires_at"' in captured["body"]
    assert b'"2026-06-01T18:00:00"' in captured["body"]
    assert b"expires_at_clear" not in captured["body"]
    assert out.id == "share-1"


@respx.mock
def test_patch_share_expiry_clear_sends_clear_flag():
    body = _share_response_json()
    body["expires_at"] = None
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=body)

    respx.patch(f"{SERVER}/api/shares/share-1").mock(side_effect=_handler)
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    out = shares_api.patch_share_expiry(api, "share-1", clear=True)
    # Body is JSON; the encoder may or may not put a space after the
    # colon. Strip spaces before checking to be encoder-agnostic.
    assert b'"expires_at_clear":true' in captured["body"].replace(b" ", b"")
    assert out.expires_at is None


def test_patch_share_expiry_rejects_both_args():
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    with pytest.raises(ValueError, match="not both"):
        shares_api.patch_share_expiry(
            api, "share-1", expires_at=datetime(2026, 6, 1), clear=True
        )


# v0.7.1 download-limit edit -------------------------------------------------


@respx.mock
def test_patch_share_download_limit_set_sends_limit():
    body = _share_response_json()
    body["download_limit"] = 10
    body["downloads_remaining"] = 10
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=body)

    respx.patch(f"{SERVER}/api/shares/share-1").mock(side_effect=_handler)
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    out = shares_api.patch_share_download_limit(api, "share-1", limit=10)
    assert b'"download_limit":10' in captured["body"].replace(b" ", b"")
    assert b"download_limit_clear" not in captured["body"]
    assert out.download_limit == 10
    assert out.downloads_remaining == 10


@respx.mock
def test_patch_share_download_limit_clear_sends_clear_flag():
    body = _share_response_json()
    body["download_limit"] = None
    body["downloads_remaining"] = None
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=body)

    respx.patch(f"{SERVER}/api/shares/share-1").mock(side_effect=_handler)
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    out = shares_api.patch_share_download_limit(api, "share-1", clear=True)
    assert b'"download_limit_clear":true' in captured["body"].replace(b" ", b"")
    assert out.download_limit is None


def test_patch_share_download_limit_rejects_both_args():
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    with pytest.raises(ValueError, match="not both"):
        shares_api.patch_share_download_limit(
            api, "share-1", limit=5, clear=True,
        )
