"""Structural guards for the v0.9.4 popup-reduction polish.

AST/substring checks (no ``ui`` import - CI lacks system tkinter), mirroring
``test_login_overlay_structure.py``. Pin the contract that the formerly-modal
Settings + recipient pickers are now in-window, and that the informational
``mb.info`` popups are gone.
"""
from __future__ import annotations

import ast
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "src" / "fileheron_client" / "ui"


def _src(name: str) -> str:
    return (UI / name).read_text(encoding="utf-8")


def _tree(name: str) -> ast.Module:
    return ast.parse(_src(name))


def _has_class(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _identifiers(tree: ast.Module) -> set[str]:
    """Names/attributes/defs used in code - ignores docstrings + comments."""
    ids: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            ids.add(node.attr)
        elif isinstance(node, ast.Name):
            ids.add(node.id)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            ids.add(node.name)
    return ids


def _base_names(cls: ast.ClassDef) -> set[str]:
    return {
        (b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", None))
        for b in cls.bases
    }


# ---- Settings is an in-window overlay, not a modal --------------------------


def test_settings_overlay_is_ctkframe() -> None:
    tree = _tree("settings_dialog.py")
    assert "CTkFrame" in _base_names(_has_class(tree, "SettingsOverlay"))
    ids = _identifiers(tree)
    for banned in ("CTkToplevel", "wait_window", "grab_set", "show_modal", "SettingsDialog"):
        assert banned not in ids, f"settings_dialog.py must not use {banned!r}"


# ---- Recipient pickers are inline, not modal --------------------------------


def test_recipient_search_is_inline() -> None:
    tree = _tree("recipient_picker.py")
    assert "CTkFrame" in _base_names(_has_class(tree, "_InlineMultiSelectPanel"))
    ids = _identifiers(tree)
    for banned in (
        "CTkToplevel", "wait_window", "grab_set", "show_modal",
        "UserPickerDialog", "GroupPickerDialog",
    ):
        assert banned not in ids, f"recipient_picker.py must not use {banned!r}"


# ---- Shared helpers exist ---------------------------------------------------


def test_widgets_has_toast_and_copy_helper() -> None:
    tree = _tree("widgets.py")
    assert "CTkLabel" in _base_names(_has_class(tree, "Toast"))
    fns = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "copy_to_clipboard_with_feedback" in fns


# ---- Informational popups are gone (toasts instead) -------------------------


def test_info_and_error_popups_are_toasts() -> None:
    # Neither info nor error popups in the upload / detail flows - they flash
    # non-modal toasts instead.
    for name in ("share_detail_view.py", "upload_panel.py"):
        assert "mb.info(" not in _src(name), f"{name}: use a toast, not mb.info"
        assert "mb.warn(" not in _src(name), f"{name}: use a toast, not mb.warn"
    # Settings + recipient pickers dropped the messagebox dependency entirely.
    for name in ("settings_dialog.py", "recipient_picker.py"):
        assert "_messagebox" not in _src(name), f"{name}: should have no popups left"


def test_end_share_confirm_stays_modal() -> None:
    # The one destructive action keeps the blocking yes/no confirm.
    assert "mb.confirm(" in _src("share_detail_view.py")
