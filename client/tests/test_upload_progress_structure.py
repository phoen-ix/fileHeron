"""Structural guards for the v0.9.x upload-progress screen.

Like the other client tests, these inspect source via AST / substring —
no ``ui`` import (CI may lack system tkinter; see conftest). They pin the
contract of the new dedicated post-send screen:

- UploadProgressView is a CTkFrame with the expected callbacks + methods
- it draws ONE progress bar per file + keeps a per-row registry
- it guards async callbacks with alive(self) and timestamps the log
- UploadPanel drills into/out of it and no longer owns the old single
  aggregate progress bar / inline pl-result card
- main_window wires the 'View in Outbox' callback
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


def test_upload_progress_view_is_ctkframe_subclass() -> None:
    cls = _has_class(_tree("upload_progress_view.py"), "UploadProgressView")
    base_names = {
        (b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", None))
        for b in cls.bases
    }
    assert "CTkFrame" in base_names, (
        f"UploadProgressView must subclass ctk.CTkFrame; got bases {base_names}"
    )


def test_upload_progress_view_constructor_contract() -> None:
    cls = _has_class(_tree("upload_progress_view.py"), "UploadProgressView")
    init = _has_method(cls, "__init__")
    pos = [a.arg for a in init.args.args]
    kw = [a.arg for a in init.args.kwonlyargs]
    assert "share" in pos and "files" in pos, (
        f"UploadProgressView.__init__ must take share + files; got {pos}"
    )
    for name in ("on_new_share", "on_view_outbox", "flash"):
        assert name in kw, (
            f"UploadProgressView.__init__ must accept keyword-only {name}; got {kw}"
        )


def test_upload_progress_view_has_expected_methods() -> None:
    cls = _has_class(_tree("upload_progress_view.py"), "UploadProgressView")
    for name in (
        "start_uploads",
        "_on_chunk_progress",
        "_on_one_done",
        "_on_one_failed",
        "_log_event",
        "_extract_pl_url",
    ):
        _has_method(cls, name)


def test_upload_progress_view_source_guards() -> None:
    src = _source("upload_progress_view.py")
    # async callbacks must early-return on a dead widget (C6).
    assert "alive(self)" in src, (
        "upload_progress_view.py callbacks must guard with alive(self)"
    )
    # timestamped activity log.
    assert "datetime.now().strftime" in src, (
        "upload_progress_view.py log must be timestamped"
    )
    # one progress bar per file + a per-row registry.
    assert "CTkProgressBar" in src
    assert "self._rows" in src, (
        "upload_progress_view.py must keep a per-file row registry (self._rows)"
    )


def test_upload_panel_drills_into_progress_view() -> None:
    cls = _has_class(_tree("upload_panel.py"), "UploadPanel")
    _has_method(cls, "_drill_in_to_progress")
    _has_method(cls, "_drill_out_to_form")
    init = _has_method(cls, "__init__")
    kw = [a.arg for a in init.args.kwonlyargs]
    assert "on_view_outbox" in kw, (
        f"UploadPanel.__init__ must accept keyword-only on_view_outbox; got {kw}"
    )


def test_upload_panel_dropped_aggregate_progress() -> None:
    """The single shared progress bar + its orchestration moved to the
    per-file UploadProgressView. Pin the migration so it can't silently
    regress to one aggregate bar in the form."""
    src = _source("upload_panel.py")
    assert "_start_uploads" not in src, (
        "upload_panel.py should no longer own _start_uploads (moved to the view)"
    )
    assert "self.progress" not in src, (
        "upload_panel.py should no longer own an aggregate self.progress bar"
    )
    assert "_build_pl_result_section" not in src, (
        "upload_panel.py should no longer own the inline pl-result card"
    )


def test_main_window_wires_view_outbox() -> None:
    src = _source("main_window.py")
    assert "on_view_outbox=self._go_to_outbox" in src, (
        "main_window.py must pass on_view_outbox into UploadPanel"
    )
    assert "def _go_to_outbox" in src, (
        "main_window.py must define _go_to_outbox"
    )
