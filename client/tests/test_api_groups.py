"""Recipient picker backend: /api/users/search + /api/groups/recipient-targets."""
from __future__ import annotations

import httpx
import respx

from fileheron_client.api import ApiClient
from fileheron_client.api import groups as groups_api
from fileheron_client.api import users as users_api

SERVER = "https://files.example.com"


@respx.mock
def test_search_users_passes_q_param():
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "items": [
                    {"user_id": 1, "display_name": "Alice", "email": "a@b.c", "role": "employee"},
                    {"user_id": 2, "display_name": "Bob",   "email": "b@b.c", "role": "client"},
                ]
            },
        )

    respx.get(f"{SERVER}/api/users/search").mock(side_effect=_handler)
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    out = users_api.search_users(api, "ali")
    assert "q=ali" in captured["url"]
    assert len(out.items) == 2
    assert out.items[0].user_id == 1
    assert out.items[0].display_name == "Alice"


@respx.mock
def test_list_recipient_groups_returns_items():
    respx.get(f"{SERVER}/api/groups/recipient-targets").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": 1, "name": "Marketing",
                        "description": "comms team",
                        "is_company_inbox": False,
                        "created_at": "2026-01-01T00:00:00",
                        "created_by_id": 1,
                        "member_count": 4,
                    },
                    {
                        "id": 2, "name": "Inbox",
                        "description": None,
                        "is_company_inbox": True,
                        "created_at": "2026-01-01T00:00:00",
                        "created_by_id": 1,
                        "member_count": 0,
                    },
                ]
            },
        )
    )
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    out = groups_api.list_recipient_groups(api)
    assert len(out.items) == 2
    assert out.items[0].name == "Marketing"
    assert out.items[1].is_company_inbox is True
    assert out.items[0].member_count == 4
