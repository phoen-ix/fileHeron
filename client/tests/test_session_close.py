"""Close-on-exit logout (client-v0.9.12) + the /current API-token lookup.

- Structural (AST / substring, no ``ui`` import — CI may lack system tkinter):
  ``AppController`` registers a WM_DELETE_WINDOW handler and ``_on_root_close``
  revokes a password session (guarded so API-token logins are a no-op).
- Behavioural (respx): ``logout`` accepts a per-call timeout; the
  ``get_current_api_token`` helper parses a 200 and tolerates 404/400.
"""
from __future__ import annotations

import ast
from pathlib import Path

import httpx
import respx

from fileheron_client.api import ApiClient
from fileheron_client.api import account as account_api
from fileheron_client.api import auth as auth_api

PKG = Path(__file__).resolve().parent.parent / "src" / "fileheron_client"
CONTROLLER = PKG / "ui" / "controller.py"
SERVER = "https://files.example.com"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _method(cls: ast.ClassDef, name: str) -> ast.FunctionDef:
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"method {cls.name}.{name} not found")


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


# ---- structural: the close hook -----------------------------------------


def test_controller_registers_wm_delete_handler() -> None:
    src = CONTROLLER.read_text(encoding="utf-8")
    assert "WM_DELETE_WINDOW" in src, (
        "AppController.start() must bind a WM_DELETE_WINDOW handler so a "
        "normal close can revoke the session"
    )


def test_on_root_close_revokes_password_session_only() -> None:
    cls = _class(_tree(CONTROLLER), "AppController")
    method = _method(cls, "_on_root_close")
    body = ast.unparse(method)
    # Revokes via logout, guards on api_token (so API-token logins no-op),
    # and still destroys the root.
    assert "logout" in body, "_on_root_close must call logout()"
    assert "api_token" in body, (
        "_on_root_close must guard on api_token so it never revokes a "
        "persistent API token on close"
    )
    assert "destroy" in body, "_on_root_close must still destroy the root"


# ---- behavioural: logout timeout ----------------------------------------


@respx.mock
def test_logout_accepts_timeout_and_clears_token() -> None:
    route = respx.post(f"{SERVER}/api/auth/logout").mock(
        return_value=httpx.Response(204)
    )
    api = ApiClient(SERVER, access_token="ACCESS")
    auth_api.logout(api, timeout=3.0)
    assert route.call_count == 1
    assert api.access_token is None


# ---- behavioural: get_current_api_token ---------------------------------


@respx.mock
def test_get_current_api_token_parses_200() -> None:
    respx.get(f"{SERVER}/api/account/api-tokens/current").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 7,
                "name": "desktop-client",
                "last4": "ab12",
                "created_at": "2026-06-01T10:00:00",
                "last_used_at": "2026-06-02T09:30:00",
                "status": "active",
            },
        )
    )
    api = ApiClient(SERVER, api_token="fh_deadbeef_" + "x" * 43)
    meta = account_api.get_current_api_token(api)
    assert meta is not None
    assert meta["name"] == "desktop-client"
    assert meta["status"] == "active"


@respx.mock
def test_get_current_api_token_tolerates_404() -> None:
    """An older server without the endpoint → None (caller falls back to the
    locally-derived prefix/last4)."""
    respx.get(f"{SERVER}/api/account/api-tokens/current").mock(
        return_value=httpx.Response(404, json={"code": "NOT_FOUND"})
    )
    api = ApiClient(SERVER, api_token="fh_deadbeef_" + "x" * 43)
    assert account_api.get_current_api_token(api) is None
