"""Structural guards for the v0.6.0 drill-down refactor.

Tests inspect the source via AST — no ``ui`` import needed (the
existing conftest is explicit that ``tests/`` must not import the ui
package because customtkinter requires system tkinter that CI may
lack). The asserts pin the contract:

- ShareDetailView is a CTkFrame (not a CTkToplevel)
- ShareListPanel owns _list_frame, _drill_in, _drill_out
- main_window no longer imports ShareDetailDialog / passes
  on_open_share
"""
from __future__ import annotations

import ast
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "src" / "fileheron_client" / "ui"


def _source(name: str) -> str:
    return (UI / name).read_text(encoding="utf-8")


def _tree(name: str) -> ast.Module:
    return ast.parse(_source(name))


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


def test_share_detail_view_is_ctkframe_subclass() -> None:
    """The drill-down refactor turned ShareDetailDialog (a class that
    wrapped a CTkToplevel) into ShareDetailView (a CTkFrame subclass).
    If a future change pulls it back into a Toplevel without updating
    ShareListPanel's drill-in path, the UI breaks."""
    tree = _tree("share_detail_view.py")
    cls = _has_class(tree, "ShareDetailView")
    base_names = {
        (b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", None))
        for b in cls.bases
    }
    assert "CTkFrame" in base_names, (
        f"ShareDetailView must subclass ctk.CTkFrame; got bases {base_names}"
    )

    # And the old name must be gone.
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            assert node.name != "ShareDetailDialog", (
                "ShareDetailDialog should have been renamed to ShareDetailView"
            )


def test_share_detail_view_constructor_takes_on_back() -> None:
    """on_back is the drill-out callback; without it the list panel
    can't restore the list when the user is done."""
    cls = _has_class(_tree("share_detail_view.py"), "ShareDetailView")
    init = _has_method(cls, "__init__")
    arg_names = [a.arg for a in init.args.args]
    kw_names = [a.arg for a in init.args.kwonlyargs]
    assert "on_back" in arg_names or "on_back" in kw_names, (
        f"ShareDetailView.__init__ must accept on_back; got args={arg_names} kw={kw_names}"
    )


def test_share_list_panel_has_drill_methods() -> None:
    cls = _has_class(_tree("share_list_panel.py"), "ShareListPanel")
    _has_method(cls, "_drill_in")
    _has_method(cls, "_drill_out")


def test_share_list_panel_no_longer_takes_on_open_share() -> None:
    """The v0.5.x ``on_open_share=callable`` kwarg is gone — drilling
    is internal to the panel now."""
    cls = _has_class(_tree("share_list_panel.py"), "ShareListPanel")
    init = _has_method(cls, "__init__")
    all_args = (
        [a.arg for a in init.args.args]
        + [a.arg for a in init.args.kwonlyargs]
    )
    assert "on_open_share" not in all_args, (
        "ShareListPanel.__init__ should not declare on_open_share anymore"
    )


def test_main_window_no_share_detail_dialog_import() -> None:
    src = _source("main_window.py")
    assert "ShareDetailDialog" not in src, (
        "main_window.py must not reference ShareDetailDialog after v0.6.0"
    )
    assert "share_detail_dialog" not in src, (
        "main_window.py must not import from share_detail_dialog after v0.6.0"
    )
    assert "on_open_share" not in src, (
        "main_window.py must not pass on_open_share to ShareListPanel"
    )
