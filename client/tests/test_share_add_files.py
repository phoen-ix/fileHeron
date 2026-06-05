"""v0.10.0 add-files-to-an-active-share: API method, MeResponse field, and
AST-structural guards on the dialog + detail view (no display needed)."""
from __future__ import annotations

import ast
from pathlib import Path

import httpx
import respx

from fileheron_client.api import ApiClient
from fileheron_client.api import shares as shares_api
from fileheron_client.models import MeResponse

SERVER = "https://files.example.com"
_SRC = Path(__file__).resolve().parent.parent / "src" / "fileheron_client"


def _share_response_json() -> dict:
    return {
        "id": "share-1",
        "kind": "outbound",
        "state": "active",
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


# ---- API client ----------------------------------------------------------


@respx.mock
def test_register_files_added_sends_notify_and_file_ids():
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json=_share_response_json())

    respx.post(f"{SERVER}/api/shares/share-1/files-added").mock(side_effect=_handler)
    api = ApiClient(SERVER, api_token="fh_xx_yy")
    out = shares_api.register_files_added(
        api, "share-1", notify=True, file_ids=["f1", "f2"],
    )
    raw = captured["body"].replace(b" ", b"")
    assert b'"notify":true' in raw
    assert b'"f1"' in raw and b'"f2"' in raw
    assert out.id == "share-1"


def test_register_files_added_is_exported():
    from fileheron_client import api as api_pkg

    assert hasattr(api_pkg, "register_files_added")


# ---- model ---------------------------------------------------------------


def test_me_response_parses_notify_default():
    base = {
        "id": 1, "email": "a@b.c", "display_name": "A",
        "role": "employee", "locale": "en",
    }
    assert MeResponse.model_validate({**base, "share_notify_recipients_default": False}).share_notify_recipients_default is False
    # Absent on an older server → defaults to True.
    assert MeResponse.model_validate(base).share_notify_recipients_default is True


# ---- structural (AST) ----------------------------------------------------


def test_add_files_dialog_is_non_blocking():
    """The dialog must NOT call wait_window — the uploads run async and need
    the Tk main loop free to process their marshalled callbacks."""
    tree = ast.parse((_SRC / "ui" / "add_files_dialog.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "wait_window", (
                "AddFilesDialog must stay non-blocking (no wait_window)."
            )


def test_add_files_dialog_reuses_upload_and_notify():
    src = (_SRC / "ui" / "add_files_dialog.py").read_text(encoding="utf-8")
    assert "start_upload(" in src        # reuses the existing upload worker
    assert "register_files_added(" in src  # calls the v1.12.0 notify endpoint


def test_share_detail_has_owner_gated_add_files():
    src = (_SRC / "ui" / "share_detail_view.py").read_text(encoding="utf-8")
    assert "def _add_files" in src
    assert "def _is_owner" in src        # owner-only gate (not _can_manage/admin)
    assert "add_files_btn" in src
