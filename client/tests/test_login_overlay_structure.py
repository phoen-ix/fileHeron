"""Structural guards for the v0.9.1 login-as-overlay refactor.

Like ``test_share_list_drilldown.py``, these inspect source via AST /
substring so no ``ui`` import is needed (CI may lack system tkinter that
customtkinter requires). They pin the new contract:

- ``LoginWindow`` (a CTkToplevel wrapper, modal via wait_window) became
  ``LoginOverlay`` - a CTkFrame placed over the root, no grab/wait_window.
- ``AppController`` owns the overlay⇄main swap, logout, and session-expiry.
- ``MainWindow`` gained teardown()/post_show() and no longer destroys the
  root on sign-out (logout returns to the overlay instead of quitting).
- ``__main__`` drives the controller, not a modal LoginWindow.
"""
from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "src" / "fileheron_client"
UI = PKG / "ui"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tree(path: Path) -> ast.Module:
    return ast.parse(_source(path))


def _has_class(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _has_method(cls: ast.ClassDef, name: str) -> ast.FunctionDef:
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"method {cls.name}.{name} not found")


# ---- LoginOverlay --------------------------------------------------------


def test_login_overlay_is_ctkframe_subclass() -> None:
    cls = _has_class(_tree(UI / "login_window.py"), "LoginOverlay")
    base_names = {
        (b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", None))
        for b in cls.bases
    }
    assert "CTkFrame" in base_names, (
        f"LoginOverlay must subclass ctk.CTkFrame; got bases {base_names}"
    )


def test_login_overlay_constructor_takes_callbacks() -> None:
    cls = _has_class(_tree(UI / "login_window.py"), "LoginOverlay")
    init = _has_method(cls, "__init__")
    kw = {a.arg for a in init.args.kwonlyargs}
    assert {"on_signed_in", "on_cancel"} <= kw, (
        f"LoginOverlay.__init__ must take on_signed_in + on_cancel; got kw={kw}"
    )


def _identifiers(tree: ast.Module) -> set[str]:
    """Every name/attribute/def identifier used in the module - ignores
    docstrings + comments (which legitimately describe the old design)."""
    ids: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            ids.add(node.attr)
        elif isinstance(node, ast.Name):
            ids.add(node.id)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            ids.add(node.name)
    return ids


def test_login_module_dropped_toplevel_modal_machinery() -> None:
    ids = _identifiers(_tree(UI / "login_window.py"))
    for banned in ("CTkToplevel", "wait_window", "grab_set", "show_modal", "LoginWindow"):
        assert banned not in ids, (
            f"login_window.py code must no longer use {banned!r} after v0.9.1"
        )


# ---- AppController -------------------------------------------------------


def test_app_controller_exists_with_transitions() -> None:
    cls = _has_class(_tree(UI / "controller.py"), "AppController")
    for m in ("start", "logout", "session_expired", "_on_signed_in", "_teardown_main"):
        _has_method(cls, m)


# ---- auto-login from a stored API token (client-v0.9.14) -----------------


def test_login_overlay_takes_auto_login_flag() -> None:
    cls = _has_class(_tree(UI / "login_window.py"), "LoginOverlay")
    init = _has_method(cls, "__init__")
    kw = {a.arg for a in init.args.kwonlyargs}
    assert "auto_login" in kw, f"LoginOverlay.__init__ must accept auto_login; got {kw}"


def test_login_overlay_has_one_shot_auto_login() -> None:
    cls = _has_class(_tree(UI / "login_window.py"), "LoginOverlay")
    body = ast.unparse(_has_method(cls, "_maybe_auto_login"))
    # One-shot (clears the flag), gated to API-token mode, and invokes sign-in.
    assert "self._auto_login = False" in body, "_maybe_auto_login must be one-shot"
    assert "api_token" in body, "_maybe_auto_login must gate on API-token mode"
    assert "_on_signin" in body, "_maybe_auto_login must trigger the sign-in path"


def test_controller_auto_logs_in_only_on_initial_show() -> None:
    src = _source(UI / "controller.py")
    # start() opts into auto-login; logout()/session_expired() must not.
    assert "auto_login=True" in src, (
        "AppController.start() must request auto_login on the initial overlay"
    )
    assert src.count("auto_login=True") == 1, (
        "only the initial start() show should auto-login (not logout / "
        "session-expiry re-shows)"
    )


# ---- MainWindow lifecycle ------------------------------------------------


def test_main_window_has_teardown_and_post_show() -> None:
    cls = _has_class(_tree(UI / "main_window.py"), "MainWindow")
    _has_method(cls, "teardown")
    _has_method(cls, "post_show")


def test_main_window_no_longer_quits_on_signout() -> None:
    src = _source(UI / "main_window.py")
    assert "_handle_signed_out" not in src, (
        "main_window.py must not keep _handle_signed_out (logout now returns "
        "to the overlay via the controller, not root.destroy())"
    )
    assert "self._app_root.destroy()" not in src, (
        "main_window.py must not destroy the root on sign-out anymore"
    )


# ---- entry point ---------------------------------------------------------


def test_main_drives_controller_not_modal_login() -> None:
    src = _source(PKG / "__main__.py")
    assert "AppController" in src, "__main__ should start the AppController"
    assert "LoginWindow" not in src, "__main__ must not import the old LoginWindow"
    assert "show_modal" not in src, "__main__ must not run a modal login loop"
